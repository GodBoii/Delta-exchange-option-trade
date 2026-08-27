from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from .errors import AppError

CapitalAllocationMode = Literal[
    "full_balance",
    "half_balance",
    "one_third_balance",
    "one_quarter_balance",
    "fixed_amount",
]

DEFAULT_ALLOCATION_MODE: CapitalAllocationMode = "half_balance"
MAX_FIXED_AMOUNT_SLOTS = 100

ALLOCATION_FRACTIONS: dict[str, Decimal] = {
    "full_balance": Decimal("1"),
    "half_balance": Decimal("0.5"),
    "one_third_balance": Decimal("1") / Decimal("3"),
    "one_quarter_balance": Decimal("0.25"),
}


@dataclass(frozen=True, slots=True)
class CapitalPolicy:
    allocation_mode: CapitalAllocationMode = DEFAULT_ALLOCATION_MODE
    capital_amount: Decimal | None = None

    def as_json(self) -> dict[str, str | None]:
        return {
            "allocationMode": self.allocation_mode,
            "capitalAmount": str(self.capital_amount) if self.capital_amount is not None else None,
        }


def decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal(default)


def policy_from_row(row: dict[str, Any] | None) -> CapitalPolicy:
    if not row:
        return CapitalPolicy()
    mode = str(row.get("allocation_mode") or DEFAULT_ALLOCATION_MODE)
    if mode == "full_balance":
        allocation_mode: CapitalAllocationMode = "full_balance"
    elif mode == "one_third_balance":
        allocation_mode = "one_third_balance"
    elif mode == "one_quarter_balance":
        allocation_mode = "one_quarter_balance"
    elif mode == "fixed_amount":
        allocation_mode = "fixed_amount"
    else:
        allocation_mode = DEFAULT_ALLOCATION_MODE
    amount = decimal_value(row.get("capital_amount")) if row.get("capital_amount") is not None else None
    return CapitalPolicy(allocation_mode=allocation_mode, capital_amount=amount)


def capital_budget(
    available: Decimal,
    total_balance: Decimal,
    allocation_mode: str,
    fixed_amount: Any = None,
) -> Decimal:
    if allocation_mode == "fixed_amount":
        requested = decimal_value(fixed_amount)
        if requested <= 0:
            raise AppError(409, "The custom capital budget is invalid", "capital_cap_invalid")
        return min(available, requested)
    fraction = ALLOCATION_FRACTIONS.get(allocation_mode)
    if fraction is None:
        raise AppError(409, "The capital allocation mode is invalid", "capital_mode_invalid")
    return min(available, total_balance * fraction)


def maximum_concurrent_strategies(
    total_balance: Decimal,
    allocation_mode: str,
    fixed_amount: Any = None,
) -> int:
    fraction = ALLOCATION_FRACTIONS.get(allocation_mode)
    if fraction is not None:
        return int(Decimal("1") // fraction)
    if allocation_mode != "fixed_amount":
        raise AppError(409, "The capital allocation mode is invalid", "capital_mode_invalid")
    requested = decimal_value(fixed_amount)
    if requested <= 0:
        raise AppError(409, "The custom capital budget is invalid", "capital_cap_invalid")
    if total_balance <= 0:
        return 1
    return max(1, min(MAX_FIXED_AMOUNT_SLOTS, int(total_balance // requested)))


def percentage_concurrency_limit(allocation_mode: str) -> int | None:
    fraction = ALLOCATION_FRACTIONS.get(allocation_mode)
    return int(Decimal("1") // fraction) if fraction is not None else None
