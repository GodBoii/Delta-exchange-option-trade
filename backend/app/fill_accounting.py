"""Allocate an exclusive strategy's exchange fills without guessing shared ownership."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import AppError


def number(value: Any, name: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise AppError(502, f"Invalid fill {name}", "fill_data_invalid") from error
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise AppError(502, f"Invalid fill {name}", "fill_data_invalid")
    return parsed


@dataclass(frozen=True, slots=True)
class PositionResult:
    product_id: int
    symbol: str
    side: str
    entry_size: Decimal
    remaining: Decimal
    entry_price: Decimal
    open_price: Decimal
    multiplier: Decimal
    realized_gross: Decimal
    fees: Decimal
    fees_complete: bool
    exit_fills: tuple[dict[str, Any], ...]

    def unrealized(self, mark: Decimal) -> Decimal:
        if not self.remaining:
            return Decimal("0")
        if not mark.is_finite() or mark < 0:
            raise AppError(502, "Invalid valuation price", "option_price_unavailable")
        return (mark - self.open_price) * self.remaining * self.multiplier


def exclusive_fill_positions(
    entries: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> dict[int, PositionResult]:
    """Require all opening fills in the scoped interval to belong to this run.

    Caller must establish exclusive product ownership for the interval. This
    function rejects additional opening activity rather than assigning its exits.
    Moving average cost is updated chronologically as opening and closing fills
    interleave. Other opening orders remain an ownership conflict.
    """
    products: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        products.setdefault(int(entry["product_id"]), []).append(entry)
    result = {}
    for product_id, orders in products.items():
        sides = {order["side"] for order in orders}
        if len(sides) != 1 or not sides <= {"buy", "sell"}:
            raise AppError(409, "Mixed ownership directions require reconciliation", "fill_ownership_unknown")
        direction = Decimal("1") if "buy" in sides else Decimal("-1")
        ids = {str(order["delta_order_id"]) for order in orders if order.get("delta_order_id")}
        if len(ids) != len(orders):
            raise AppError(409, "Entry order identities are incomplete", "fill_ownership_unknown")
        multipliers = {number(order.get("contract_value"), "multiplier", positive=True) for order in orders}
        if len(multipliers) != 1:
            raise AppError(409, "Contract multipliers disagree", "fill_data_invalid")
        multiplier = multipliers.pop()
        unique: dict[str, dict[str, Any]] = {}
        for fill in fills:
            if int(fill.get("product_id") or 0) != product_id:
                continue
            identity = str(fill.get("id") or "")
            if not identity:
                raise AppError(502, "Exchange fill identity missing", "fill_data_invalid")
            if identity in unique and unique[identity] != fill:
                raise AppError(502, "Conflicting copies of an exchange fill", "fill_data_invalid")
            unique[identity] = fill
        try:
            ordered = sorted(
                unique.values(), key=lambda fill: datetime.fromisoformat(str(fill["created_at"]).replace("Z", "+00:00"))
            )
        except (KeyError, ValueError, TypeError) as error:
            raise AppError(502, "Fill chronology unavailable", "fill_data_invalid") from error
        size = remaining = entry_value = open_value = gross = fees = Decimal("0")
        fees_complete = True
        exits: list[dict[str, Any]] = []
        for fill in ordered:
            quantity = number(fill.get("size"), "quantity", positive=True)
            price = number(fill.get("price"), "price")
            if quantity != quantity.to_integral_value() or price < 0 or fill.get("side") not in {"buy", "sell"}:
                raise AppError(502, "Invalid fill price, quantity or direction", "fill_data_invalid")
            if fill.get("commission") is None:
                fees_complete = False
                fee = Decimal("0")
            else:
                fee = number(fill["commission"], "commission")
            if str(fill.get("order_id")) in ids:
                if fill["side"] not in sides:
                    raise AppError(409, "Entry fill direction disagrees", "fill_ownership_unknown")
                size += quantity
                remaining += quantity
                entry_value += price * quantity
                open_value += price * quantity
            else:
                if fill["side"] in sides or quantity > remaining or not size:
                    raise AppError(409, "External fill cannot be exclusively attributed", "fill_ownership_unknown")
                cost = open_value / remaining
                gross += (price - cost) * direction * quantity * multiplier
                open_value -= cost * quantity
                remaining -= quantity
                exits.append(fill)
            fees += fee
        expected = sum((number(order.get("filled_size"), "entry quantity") for order in orders), Decimal("0"))
        if size != expected or expected < 0:
            raise AppError(409, "Entry fills are incomplete", "fills_incomplete")
        result[product_id] = PositionResult(
            product_id=product_id,
            symbol=str(orders[0].get("product_symbol") or product_id),
            side=orders[0]["side"],
            entry_size=size,
            remaining=remaining * direction,
            entry_price=entry_value / size if size else Decimal("0"),
            open_price=open_value / remaining if remaining else Decimal("0"),
            multiplier=multiplier,
            realized_gross=gross,
            fees=fees,
            fees_complete=fees_complete,
            exit_fills=tuple(exits),
        )
    return result
