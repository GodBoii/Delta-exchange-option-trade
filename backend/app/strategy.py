from datetime import date
from decimal import Decimal
from typing import Any

from .errors import AppError
from .models import StrategyDefinition, StrategyLeg


def delta_expiry(value: date | str) -> str:
    parsed = date.fromisoformat(value) if isinstance(value, str) else value
    return parsed.strftime("%d-%m-%Y")


def combined_premium_metrics(legs: list[dict[str, Any]], stop_percent: Decimal) -> dict[str, Decimal]:
    entry_credit = Decimal("0")
    close_cost = Decimal("0")
    for leg in legs:
        direction = Decimal("1") if leg["side"] == "sell" else Decimal("-1")
        weight = Decimal(str(leg["filled_size"])) * Decimal(str(leg["contract_value"]))
        entry_credit += direction * Decimal(str(leg["entry_price"])) * weight
        close_cost += direction * Decimal(str(leg["mark_price"])) * weight
    trigger_close_cost = entry_credit * (Decimal("1") + stop_percent / Decimal("100"))
    return {
        "entry_credit": entry_credit,
        "close_cost": close_cost,
        "loss": close_cost - entry_credit,
        "trigger_close_cost": trigger_close_cost,
    }


def strategy_level_metrics(
    legs: list[dict[str, Any]],
    *,
    risk_basis: str,
    stop_percent: Decimal,
    take_profit_percent: Decimal,
) -> dict[str, Decimal | str | bool]:
    signed_entry = Decimal("0")
    signed_current = Decimal("0")
    for leg in legs:
        direction = Decimal("1") if leg["side"] == "sell" else Decimal("-1")
        weight = Decimal(str(leg["filled_size"])) * Decimal(str(leg["contract_value"]))
        signed_entry += direction * Decimal(str(leg["entry_price"])) * weight
        signed_current += direction * Decimal(str(leg["mark_price"])) * weight

    stop_ratio = stop_percent / Decimal("100")
    target_ratio = take_profit_percent / Decimal("100")
    if risk_basis == "net_debit":
        entry_value = -signed_entry
        current_value = -signed_current
        profit = current_value - entry_value
        stop_value = max(Decimal("0"), entry_value * (Decimal("1") - stop_ratio))
        target_value = entry_value * (Decimal("1") + target_ratio)
        stop_triggered = current_value <= stop_value
        target_triggered = current_value >= target_value
        current_label = "liquidation_value"
    else:
        entry_value = signed_entry
        current_value = signed_current
        profit = entry_value - current_value
        stop_value = entry_value * (Decimal("1") + stop_ratio)
        target_value = max(Decimal("0"), entry_value * (Decimal("1") - target_ratio))
        stop_triggered = current_value >= stop_value
        target_triggered = current_value <= target_value
        current_label = "close_cost"

    return {
        "entry_value": entry_value,
        "current_value": current_value,
        "profit": profit,
        "stop_value": stop_value,
        "target_value": target_value,
        "stop_triggered": stop_triggered,
        "target_triggered": target_triggered,
        "current_label": current_label,
    }


def resolve_leg(leg: StrategyLeg, chain: list[dict[str, Any]]) -> dict[str, Any]:
    contract_type = "call_options" if leg.optionType == "call" else "put_options"
    candidates: list[tuple[dict[str, Any], float]] = []
    for item in chain:
        if item.get("contract_type") != contract_type or item.get("strike_price") is None:
            continue
        try:
            candidates.append((item, float(item["strike_price"])))
        except (TypeError, ValueError):
            continue
    candidates.sort(key=lambda candidate: candidate[1])
    if not candidates:
        raise AppError(422, f"No live {leg.optionType} options found for {leg.expiry}", "option_chain_empty")
    try:
        spot = float(next(item.get("spot_price") for item, _ in candidates if item.get("spot_price") is not None))
    except (StopIteration, TypeError, ValueError) as exc:
        raise AppError(422, "Option chain did not include a spot price", "spot_price_missing") from exc

    if leg.strikeMode == "exact":
        index = next((idx for idx, (_, strike) in enumerate(candidates) if strike == leg.exactStrike), -1)
        if index < 0:
            raise AppError(422, f"Strike {leg.exactStrike} is not listed", "strike_not_found")
    else:
        atm_index = min(range(len(candidates)), key=lambda idx: abs(candidates[idx][1] - spot))
        if leg.strikeMode == "atm":
            direction = 0
        elif leg.optionType == "call":
            direction = 1 if leg.strikeMode == "otm" else -1
        else:
            direction = -1 if leg.strikeMode == "otm" else 1
        index = max(0, min(len(candidates) - 1, atm_index + direction * leg.strikeSteps))

    selected, strike = candidates[index]
    return {
        **leg.model_dump(mode="json", exclude_none=True),
        "productId": int(selected["product_id"]),
        "productSymbol": str(selected["symbol"]),
        "strike": strike,
        "markPrice": selected.get("mark_price"),
    }


def deferred_control_warnings(definition: StrategyDefinition) -> list[str]:
    controls: list[str] = []
    if definition.overallTarget:
        controls.append("overall target")
    if definition.overallStopLoss and definition.riskMode != "combined_premium":
        controls.append("overall stop loss")
    if definition.trailToBreakEven:
        controls.append("cross-leg break-even trailing")
    if any(leg.reentryOnTarget or leg.reentryOnStop for leg in definition.legs):
        controls.append("automatic re-entry")
    return controls
