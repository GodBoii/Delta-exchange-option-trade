from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import AppError
from .fill_accounting import number
from .models import StrategyDefinition, StrategyLeg


def delta_expiry(value: date | str) -> str:
    parsed = date.fromisoformat(value) if isinstance(value, str) else value
    return parsed.strftime("%d-%m-%Y")


def maximum_expiry_loss(legs: list[dict[str, Any]]) -> Decimal:
    """Exact gross payoff bound for linear options sharing an underlying and expiry."""
    if not legs or len({leg.get("expiry") for leg in legs}) != 1 or not legs[0].get("expiry"):
        raise AppError(422, "Maximum-loss calculation requires one known expiry", "unsupported_payoff")
    positions = []
    credit = Decimal("0")
    for leg in legs:
        if leg.get("side") not in {"buy", "sell"} or leg.get("option_type") not in {"call", "put"}:
            raise AppError(422, "Unsupported maximum-loss leg", "unsupported_payoff")
        strike = number(leg.get("strike"), "strike", positive=True)
        quantity = number(leg.get("filled_size"), "quantity", positive=True)
        multiplier = number(leg.get("contract_value"), "multiplier", positive=True)
        price = number(leg.get("entry_price"), "price")
        if price < 0 or quantity != quantity.to_integral_value():
            raise AppError(422, "Invalid payoff price or quantity", "unsupported_payoff")
        signed_weight = quantity * multiplier * (1 if leg["side"] == "buy" else -1)
        credit -= price * signed_weight
        positions.append((strike, signed_weight, leg["option_type"]))
    if sum((weight for _, weight, kind in positions if kind == "call"), Decimal("0")) < 0:
        raise AppError(422, "The call exposure has unbounded loss", "unbounded_payoff")
    worst = min(
        credit
        + sum(
            (
                weight * max(Decimal("0"), spot - strike if kind == "call" else strike - spot)
                for strike, weight, kind in positions
            ),
            Decimal("0"),
        )
        for spot in {Decimal("0"), *(strike for strike, _, _ in positions)}
    )
    return max(Decimal("0"), -worst)


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
        remaining_weight = Decimal(str(leg.get("remaining_size", leg["filled_size"]))) * Decimal(
            str(leg["contract_value"])
        )
        signed_current += (
            direction * Decimal(str(leg["mark_price"])) * remaining_weight
            + direction * Decimal(str(leg["entry_price"])) * weight
            - direction * Decimal(str(leg.get("remaining_entry_price", leg["entry_price"]))) * remaining_weight
            - Decimal(str(leg.get("realized_gross", "0")))
        )

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
        loss_basis = maximum_expiry_loss(legs) if risk_basis == "defined_max_loss" else entry_value
        stop_value = entry_value + loss_basis * stop_ratio
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
        index = atm_index + direction * leg.strikeSteps
        if not 0 <= index < len(candidates):
            raise AppError(422, "The requested strike distance is not listed", "strike_not_found")

    selected, strike = candidates[index]
    quotes = selected.get("quotes") if isinstance(selected.get("quotes"), dict) else {}
    return {
        **leg.model_dump(mode="json", exclude_none=True),
        "productId": int(selected["product_id"]),
        "productSymbol": str(selected["symbol"]),
        "strike": strike,
        "spotPrice": selected.get("spot_price"),
        "markPrice": selected.get("mark_price"),
        "bestBid": quotes.get("best_bid"),
        "bestAsk": quotes.get("best_ask"),
    }


def deferred_control_warnings(definition: StrategyDefinition) -> list[str]:
    controls: list[str] = []
    if definition.overallTarget:
        controls.append("overall target")
    if definition.overallStopLoss:
        controls.append("overall stop loss")
    if definition.trailToBreakEven:
        controls.append("cross-leg break-even trailing")
    if any(leg.reentryOnTarget or leg.reentryOnStop for leg in definition.legs):
        controls.append("automatic re-entry")
    return controls


def validate_entry_policy(definition: StrategyDefinition, resolved: list[dict[str, Any]]) -> None:
    """Reject unsupported new exposure without making old history unreadable."""
    deferred = deferred_control_warnings(definition)
    if deferred:
        raise AppError(422, f"Execution does not support: {', '.join(deferred)}", "unsupported_risk_control")
    if (
        definition.combinedStopLossPercent is not None
        and definition.combinedStopLossPercent != definition.stopLossPercent
    ):
        raise AppError(422, "The two stop percentages disagree", "conflicting_stop_percentages")
    if definition.instrument.index != f"{definition.instrument.underlying}USD":
        raise AppError(422, "Instrument and underlying disagree", "instrument_mismatch")
    if len({leg.id for leg in definition.legs}) != len(definition.legs):
        raise AppError(422, "Leg identifiers must be unique", "duplicate_leg_id")
    if len({leg["productId"] for leg in resolved}) != len(resolved):
        raise AppError(422, "Each leg must resolve to a different contract", "duplicate_contract")
    if definition.riskBasis != "net_debit" and definition.takeProfitPercent > 100:
        raise AppError(422, "Credit take profit cannot exceed entry credit", "invalid_profit_target")
    for leg in resolved:
        if leg["position"] != "sell":
            continue
        try:
            mark = Decimal(str(leg.get("markPrice")))
        except (InvalidOperation, TypeError, ValueError):
            mark = Decimal("NaN")
        if not mark.is_finite() or mark <= 0:
            raise AppError(409, "A valid short-option mark is required before entry", "option_price_unavailable")
        if (
            definition.riskMode != "legwise"
            and definition.emergencyExitEnabled
            and not definition.emergencyStopLossPercent
        ):
            raise AppError(422, "Enabled emergency protection requires a percentage", "emergency_policy_incomplete")
