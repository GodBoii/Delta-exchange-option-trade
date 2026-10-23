from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from .errors import AppError

CapitalAllocationMode = Literal[
    "full_balance",
    "half_balance",
    "one_third_balance",
    "one_quarter_balance",
    "fixed_amount",
    "equal_parts",
    "custom_percentage",
]

DEFAULT_ALLOCATION_MODE: CapitalAllocationMode = "half_balance"
MAX_FIXED_AMOUNT_SLOTS = 100

ALLOCATION_DIVISORS = {
    "full_balance": 1,
    "half_balance": 2,
    "one_third_balance": 3,
    "one_quarter_balance": 4,
}


@dataclass(frozen=True, slots=True)
class CapitalPolicy:
    allocation_mode: CapitalAllocationMode = DEFAULT_ALLOCATION_MODE
    capital_amount: Decimal | None = None
    allocation_percent: Decimal | None = None
    division_count: int | None = None
    asset_overrides: dict[str, "CapitalPolicy"] = field(default_factory=dict)
    overall_limit: dict[str, Any] | None = None
    asset_limits: dict[str, dict[str, Any]] = field(default_factory=dict)
    maximum_strategies: int | None = None
    revision: str | None = None
    allow_smaller_entries: bool = True

    def for_asset(self, asset: str) -> "CapitalPolicy":
        return self.asset_overrides.get(asset, self)

    def concurrency_limit(self, total: Decimal | None = None) -> int | None:
        if self.maximum_strategies is not None:
            return self.maximum_strategies
        if self.asset_overrides:
            return MAX_FIXED_AMOUNT_SLOTS
        if total is not None:
            return maximum_concurrent_strategies(
                total,
                self.allocation_mode,
                self.capital_amount,
                allocation_percent=self.allocation_percent,
                division_count=self.division_count,
            )
        if self.allocation_mode == "equal_parts":
            return self.division_count
        if self.allocation_mode == "custom_percentage" and self.allocation_percent:
            return min(MAX_FIXED_AMOUNT_SLOTS, int(Decimal(100) // self.allocation_percent))
        return percentage_concurrency_limit(self.allocation_mode)

    def as_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "allocationMode": self.allocation_mode,
            "capitalAmount": str(self.capital_amount) if self.capital_amount is not None else None,
        }
        if self.allocation_percent is not None:
            result["allocationPercent"] = str(self.allocation_percent)
        if self.division_count is not None:
            result["divisionCount"] = self.division_count
        if self.asset_overrides:
            result["assetOverrides"] = {asset: rule.as_json() for asset, rule in self.asset_overrides.items()}
        if self.overall_limit:
            result["overallLimit"] = self.overall_limit
        if self.asset_limits:
            result["assetLimits"] = self.asset_limits
        if self.maximum_strategies is not None:
            result["maximumConcurrentStrategies"] = self.maximum_strategies
        if not self.allow_smaller_entries:
            result["allowSmallerEntries"] = False
        return result


def decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal(default)


def policy_from_row(row: dict[str, Any] | None) -> CapitalPolicy:
    if not row:
        return CapitalPolicy()
    if "allocationMode" in row:
        fields = {
            "allocationMode": "allocation_mode", "capitalAmount": "capital_amount",
            "allocationPercent": "allocation_percent", "divisionCount": "division_count",
            "assetOverrides": "asset_overrides", "overallLimit": "overall_limit",
            "assetLimits": "asset_limits", "maximumConcurrentStrategies": "maximum_concurrent_strategies",
            "allowSmallerEntries": "allow_smaller_entries",
        }
        row = {fields[key]: value for key, value in row.items() if key in fields}
    mode = str(row.get("allocation_mode") or DEFAULT_ALLOCATION_MODE)
    if mode == "full_balance":
        allocation_mode: CapitalAllocationMode = "full_balance"
    elif mode == "one_third_balance":
        allocation_mode = "one_third_balance"
    elif mode == "one_quarter_balance":
        allocation_mode = "one_quarter_balance"
    elif mode == "fixed_amount":
        allocation_mode = "fixed_amount"
    elif mode == "equal_parts":
        allocation_mode = "equal_parts"
    elif mode == "custom_percentage":
        allocation_mode = "custom_percentage"
    else:
        allocation_mode = DEFAULT_ALLOCATION_MODE
    amount = decimal_value(row.get("capital_amount")) if row.get("capital_amount") is not None else None
    return CapitalPolicy(
        allocation_mode=allocation_mode,
        capital_amount=amount,
        allocation_percent=decimal_value(row["allocation_percent"])
        if row.get("allocation_percent") is not None
        else None,
        division_count=row.get("division_count"),
        asset_overrides={
            asset: policy_from_row(
                {
                    "allocation_mode": rule["allocationMode"],
                    "capital_amount": rule.get("capitalAmount"),
                    "allocation_percent": rule.get("allocationPercent"),
                    "division_count": rule.get("divisionCount"),
                }
            )
            for asset, rule in (row.get("asset_overrides") or {}).items()
        },
        overall_limit=row.get("overall_limit"),
        asset_limits=row.get("asset_limits") or {},
        maximum_strategies=row.get("maximum_concurrent_strategies"),
        revision=row.get("_revision"),
        allow_smaller_entries=row.get("allow_smaller_entries", True),
    )


def capital_budget(
    available: Decimal,
    total_balance: Decimal,
    allocation_mode: str,
    fixed_amount: Any = None,
    *,
    allocation_percent: Decimal | None = None,
    division_count: int | None = None,
    allow_smaller_entries: bool = True,
) -> Decimal:
    def available_budget(requested: Decimal) -> Decimal:
        return min(available, requested) if allow_smaller_entries or available >= requested else Decimal(0)

    if allocation_mode == "fixed_amount":
        requested = decimal_value(fixed_amount)
        if not requested.is_finite() or requested <= 0:
            raise AppError(409, "The custom capital budget is invalid", "capital_cap_invalid")
        return available_budget(requested)
    if allocation_mode == "custom_percentage":
        if allocation_percent is None or not allocation_percent.is_finite() or not 0 < allocation_percent <= 100:
            raise AppError(409, "The capital percentage is invalid", "capital_cap_invalid")
        return available_budget(total_balance * allocation_percent / Decimal(100))
    divisor = division_count if allocation_mode == "equal_parts" else ALLOCATION_DIVISORS.get(allocation_mode)
    if allocation_mode == "equal_parts" and (not isinstance(divisor, int) or not 1 <= divisor <= 100):
        raise AppError(409, "The capital division count is invalid", "capital_cap_invalid")
    if divisor is None:
        raise AppError(409, "The capital allocation mode is invalid", "capital_mode_invalid")
    return available_budget(total_balance / divisor)


def maximum_concurrent_strategies(
    total_balance: Decimal,
    allocation_mode: str,
    fixed_amount: Any = None,
    *,
    allocation_percent: Decimal | None = None,
    division_count: int | None = None,
) -> int:
    if allocation_mode in {"equal_parts", "custom_percentage"}:
        capital_budget(
            total_balance,
            total_balance,
            allocation_mode,
            allocation_percent=allocation_percent,
            division_count=division_count,
        )
        if allocation_mode == "equal_parts":
            return int(division_count)
        return max(1, min(MAX_FIXED_AMOUNT_SLOTS, int(Decimal(100) // allocation_percent)))
    divisor = ALLOCATION_DIVISORS.get(allocation_mode)
    if divisor is not None:
        return divisor
    if allocation_mode != "fixed_amount":
        raise AppError(409, "The capital allocation mode is invalid", "capital_mode_invalid")
    requested = decimal_value(fixed_amount)
    if not requested.is_finite() or requested <= 0:
        raise AppError(409, "The custom capital budget is invalid", "capital_cap_invalid")
    if total_balance <= 0:
        return 1
    return max(1, min(MAX_FIXED_AMOUNT_SLOTS, int(total_balance // requested)))


def percentage_concurrency_limit(allocation_mode: str) -> int | None:
    return ALLOCATION_DIVISORS.get(allocation_mode)


def policy_budget(available: Decimal, total: Decimal, policy: CapitalPolicy, asset: str | None = None) -> Decimal:
    rule = policy.for_asset(asset) if asset is not None else policy
    return capital_budget(
        available,
        total,
        rule.allocation_mode,
        rule.capital_amount,
        allocation_percent=rule.allocation_percent,
        division_count=rule.division_count,
        allow_smaller_entries=policy.allow_smaller_entries,
    )


def requested_budget(total: Decimal, policy: CapitalPolicy, asset: str | None = None) -> Decimal:
    rule = policy.for_asset(asset) if asset is not None else policy
    return capital_budget(
        max(total, rule.capital_amount or Decimal(0)), total, rule.allocation_mode, rule.capital_amount,
        allocation_percent=rule.allocation_percent, division_count=rule.division_count,
    )


def limit_budget(total: Decimal, limit: dict[str, Any] | None) -> Decimal:
    if limit is None:
        return total
    value = decimal_value(limit.get("value"))
    if not value.is_finite() or value <= 0 or limit.get("mode") not in {"percentage", "fixed_amount"}:
        raise AppError(409, "The aggregate capital limit is invalid", "capital_cap_invalid")
    return min(total, total * value / Decimal(100) if limit["mode"] == "percentage" else value)


def fits_capital_limits(
    policy: CapitalPolicy,
    total: Decimal,
    asset: str,
    budget: Decimal,
    reserved: Decimal,
    reserved_by_asset: dict[str, Decimal],
) -> bool:
    return reserved + budget <= limit_budget(total, policy.overall_limit) and reserved_by_asset.get(
        asset, Decimal(0)
    ) + budget <= limit_budget(total, policy.asset_limits.get(asset))


def has_available_capital_slot(
    available: Decimal,
    total_balance: Decimal,
    policy: CapitalPolicy,
    occupied_slots: list[int],
    reserved_budget: Decimal,
    asset: str = "BTC",
    reserved_by_asset: dict[str, Decimal] | None = None,
) -> bool:
    """Check the same slot and account-budget limits used by entry reservation."""
    if available <= 0 or total_balance <= 0:
        return False
    maximum = policy.concurrency_limit(total_balance)
    if len(occupied_slots) >= maximum:
        return False
    budget = policy_budget(available, total_balance, policy, asset)
    return budget > 0 and fits_capital_limits(
        policy, total_balance, asset, budget, reserved_budget, reserved_by_asset or {}
    )
