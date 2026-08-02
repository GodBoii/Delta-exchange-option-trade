from datetime import date
from typing import Any

from .errors import AppError
from .models import StrategyDefinition, StrategyLeg


def delta_expiry(value: date | str) -> str:
    parsed = date.fromisoformat(value) if isinstance(value, str) else value
    return parsed.strftime("%d-%m-%Y")


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
    if definition.overallStopLoss:
        controls.append("overall stop loss")
    if definition.trailToBreakEven:
        controls.append("cross-leg break-even trailing")
    if any(leg.reentryOnTarget or leg.reentryOnStop for leg in definition.legs):
        controls.append("automatic re-entry")
    return controls
