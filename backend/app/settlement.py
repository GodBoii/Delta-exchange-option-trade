"""Money view of a strategy run, rebuilt from its recorded orders.

Pure functions only. The engine, the run detail view and the owner ledger all use
these, so a run's P&L is computed one way wherever it is shown.
"""

from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def slippage_fields(side: str, reference: Any, average: Any) -> dict[str, str]:
    """
    Execution slippage against the mark price observed before submission.

    The sign is normalised so positive always means adverse: a buy that filled
    above the reference, or a sell that filled below it. Without a usable
    reference or fill price there is nothing honest to record, so nothing is.
    """
    reference_price = optional_decimal(reference)
    average_price = optional_decimal(average)
    if not reference_price or not average_price or reference_price <= 0 or average_price <= 0:
        return {}
    direction = Decimal("1") if side == "buy" else Decimal("-1")
    slippage = (average_price - reference_price) * direction
    return {
        "slippage": str(slippage),
        "slippage_percent": str(slippage / reference_price * Decimal("100")),
    }


def filled_size(order: dict[str, Any]) -> Decimal:
    """Filled lots; a closed order without a recorded fill size filled completely."""
    filled = decimal_value(order.get("filled_size"))
    if filled <= 0 and str(order.get("state")) == "closed":
        filled = decimal_value(order.get("size"))
    return filled


def order_cash_flow(order: dict[str, Any]) -> Decimal:
    """
    Signed premium moved by one recorded order, in quote currency.

    Selling collects premium (positive), buying pays it (negative). Contract
    value converts lots into underlying units; it defaults to 1 so pre-migration
    rows still produce a directionally correct figure.
    """
    filled = filled_size(order)
    price = optional_decimal(order.get("average_fill_price")) or Decimal("0")
    contract_value = optional_decimal(order.get("contract_value")) or Decimal("1")
    direction = Decimal("1") if order.get("side") == "sell" else Decimal("-1")
    return direction * price * filled * contract_value


def settlement_summary(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Money view of a run, rebuilt from the recorded orders rather than stored
    running totals, so it is identical whether it is computed at exit time or
    when the Information panel is opened months later.
    """
    entry_premium = Decimal("0")
    exit_premium = Decimal("0")
    commission = Decimal("0")
    slippage_cost = Decimal("0")
    requested_lots = Decimal("0")
    filled_lots = Decimal("0")
    closed_lots = Decimal("0")
    symbols: dict[str, dict[str, Decimal]] = {}

    for order in orders:
        is_exit = str(order.get("kind")) == "exit"
        cash = order_cash_flow(order)
        filled = filled_size(order)
        commission += decimal_value(order.get("commission"))
        slippage = optional_decimal(order.get("slippage"))
        contract_value = optional_decimal(order.get("contract_value")) or Decimal("1")
        if slippage is not None:
            slippage_cost += slippage * filled * contract_value
        if is_exit:
            exit_premium += cash
            closed_lots += filled
        else:
            entry_premium += cash
            requested_lots += decimal_value(order.get("size"))
            filled_lots += filled
        symbol = str(order.get("product_symbol") or "unknown")
        bucket = symbols.setdefault(
            symbol,
            {
                "entryPremium": Decimal("0"),
                "exitPremium": Decimal("0"),
                "commission": Decimal("0"),
                "entryLots": Decimal("0"),
                "exitLots": Decimal("0"),
            },
        )
        bucket["exitPremium" if is_exit else "entryPremium"] += cash
        bucket["exitLots" if is_exit else "entryLots"] += filled
        bucket["commission"] += decimal_value(order.get("commission"))

    gross = entry_premium + exit_premium
    return {
        "entryPremium": str(entry_premium),
        "exitPremium": str(exit_premium),
        "grossPnl": str(gross),
        "commission": str(commission),
        "realizedPnl": str(gross - commission),
        "slippageCost": str(slippage_cost),
        "requestedLots": str(requested_lots),
        "filledLots": str(filled_lots),
        "closedLots": str(closed_lots),
        "fullyClosed": bool(
            filled_lots > 0 and all(bucket["entryLots"] == bucket["exitLots"] for bucket in symbols.values())
        ),
        "bySymbol": [
            {
                "symbol": symbol,
                "entryPremium": str(bucket["entryPremium"]),
                "exitPremium": str(bucket["exitPremium"]),
                "commission": str(bucket["commission"]),
                "entryLots": str(bucket["entryLots"]),
                "exitLots": str(bucket["exitLots"]),
                "realizedPnl": str(bucket["entryPremium"] + bucket["exitPremium"] - bucket["commission"]),
            }
            for symbol, bucket in sorted(symbols.items())
        ],
    }


def run_settlement(stored: dict[str, Any], orders: list[dict[str, Any]]) -> dict[str, Any]:
    """The settlement the run detail shows: stored exchange-fill accounting, else rebuilt from orders."""
    settlement = dict(stored)
    if orders and stored.get("accountingBasis") != "allocated_exchange_fills":
        settlement.update(settlement_summary(orders))
    return settlement
