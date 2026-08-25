from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import StrategyDefinition

IST = ZoneInfo("Asia/Kolkata")


def _schedule(now: datetime) -> tuple[str, str]:
    local_now = now.astimezone(IST)
    entry = (local_now + timedelta(minutes=15)).replace(second=0, microsecond=0)
    exit_at = entry + timedelta(hours=7)
    return entry.astimezone(UTC).isoformat(), exit_at.astimezone(UTC).isoformat()


def _fallback_expiry(now: datetime, policy: str) -> str:
    days = {"same_day": 1, "next_day": 2, "7_day": 7, "30_day": 30}[policy]
    return (now.astimezone(IST).date() + timedelta(days=days)).isoformat()


def _leg(
    key: str,
    *,
    position: str,
    option_type: str,
    role: str,
    expiry: str,
    strike_mode: str = "atm",
    strike_steps: int = 0,
) -> dict[str, Any]:
    return {
        "id": key,
        "lots": 1,
        "position": position,
        "optionType": option_type,
        "expiry": expiry,
        "strikeMode": strike_mode,
        "strikeSteps": strike_steps,
        "orderType": "market_order",
        "role": role,
        "reentryOnTarget": 0,
        "reentryOnStop": 0,
    }


def _base(
    now: datetime,
    *,
    name: str,
    description: str,
    category: str,
    outlook: str,
    expiry_policy: str,
    holding_mode: str,
    risk_basis: str,
    risk_mode: str,
    legs: list[dict[str, Any]],
) -> dict[str, Any]:
    entry_at, exit_at = _schedule(now)
    return {
        "schemaVersion": 2,
        "version": 1,
        "name": name,
        "description": description,
        "category": category,
        "marketOutlook": outlook,
        "enabledForAi": True,
        "instrument": {"index": "BTCUSD", "underlying": "BTC", "underlyingFrom": "cash"},
        "entry": {"strategyType": "intraday", "entryAt": entry_at, "exitAt": exit_at},
        "holdingMode": holding_mode,
        "expiryPolicy": expiry_policy,
        "exitMinutesBeforeExpiry": 5,
        "sameExpiryRequired": True,
        "squareOff": "complete",
        "riskMode": risk_mode,
        "riskBasis": risk_basis,
        "stopLossPercent": 100,
        "takeProfitPercent": 50,
        "combinedStopLossPercent": 100 if risk_mode == "combined_premium" else None,
        "emergencyStopLossPercent": 300 if risk_basis != "net_debit" else None,
        "emergencyExitEnabled": True,
        "trailToBreakEven": False,
        "breakEvenScope": "all_legs",
        "allocationMode": "one_of_three_account_slots",
        "lotsMode": "auto",
        "maximumLots": 1,
        "equalLotsRequired": len(legs) > 1,
        "legs": legs,
        "acknowledgement": True,
    }


def default_strategy_definitions(now: datetime | None = None) -> list[StrategyDefinition]:
    now = now or datetime.now(UTC)

    seven_day = _fallback_expiry(now, "7_day")
    same_day = _fallback_expiry(now, "same_day")

    definitions = [
        _base(
            now,
            name="Long call",
            description=(
                "Buy an ATM call when BTC has a strong bullish forecast and the expected move exceeds "
                "the premium hurdle."
            ),
            category="premium_buying",
            outlook="bullish",
            expiry_policy="7_day",
            holding_mode="intraday",
            risk_basis="net_debit",
            risk_mode="strategy_level",
            legs=[_leg("long-call", position="buy", option_type="call", role="long_call", expiry=seven_day)],
        ),
        _base(
            now,
            name="Long put",
            description=(
                "Buy an ATM put when BTC has a strong bearish forecast and the expected fall exceeds "
                "the premium hurdle."
            ),
            category="premium_buying",
            outlook="bearish",
            expiry_policy="7_day",
            holding_mode="intraday",
            risk_basis="net_debit",
            risk_mode="strategy_level",
            legs=[_leg("long-put", position="buy", option_type="put", role="long_put", expiry=seven_day)],
        ),
        _base(
            now,
            name="Long ATM straddle",
            description=(
                "Buy the same-expiry ATM call and put when a large move is likely but its direction is unclear."
            ),
            category="premium_buying",
            outlook="large_move_unknown_direction",
            expiry_policy="same_day",
            holding_mode="intraday",
            risk_basis="net_debit",
            risk_mode="strategy_level",
            legs=[
                _leg("long-straddle-call", position="buy", option_type="call", role="long_call", expiry=same_day),
                _leg("long-straddle-put", position="buy", option_type="put", role="long_put", expiry=same_day),
            ],
        ),
        _base(
            now,
            name="Long strangle",
            description=(
                "Buy OTM calls and puts when a very large move is expected and a lower initial debit is preferred."
            ),
            category="premium_buying",
            outlook="very_large_move_unknown_direction",
            expiry_policy="7_day",
            holding_mode="intraday",
            risk_basis="net_debit",
            risk_mode="strategy_level",
            legs=[
                _leg(
                    "long-strangle-call",
                    position="buy",
                    option_type="call",
                    role="long_call",
                    expiry=seven_day,
                    strike_mode="otm",
                    strike_steps=2,
                ),
                _leg(
                    "long-strangle-put",
                    position="buy",
                    option_type="put",
                    role="long_put",
                    expiry=seven_day,
                    strike_mode="otm",
                    strike_steps=2,
                ),
            ],
        ),
        _base(
            now,
            name="Short ATM straddle",
            description=(
                "Sell the same-expiry ATM call and put on a quiet day with strong sideways evidence and low event risk."
            ),
            category="premium_selling",
            outlook="sideways",
            expiry_policy="same_day",
            holding_mode="hold_to_expiry",
            risk_basis="net_credit",
            risk_mode="combined_premium",
            legs=[
                _leg("short-straddle-call", position="sell", option_type="call", role="short_call", expiry=same_day),
                _leg("short-straddle-put", position="sell", option_type="put", role="short_put", expiry=same_day),
            ],
        ),
        _base(
            now,
            name="Short strangle",
            description=(
                "Sell OTM calls and puts when BTC is expected to remain inside a wider range and volatility "
                "is low or falling."
            ),
            category="premium_selling",
            outlook="wide_sideways",
            expiry_policy="same_day",
            holding_mode="hold_to_expiry",
            risk_basis="net_credit",
            risk_mode="combined_premium",
            legs=[
                _leg(
                    "short-strangle-call",
                    position="sell",
                    option_type="call",
                    role="short_call",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=2,
                ),
                _leg(
                    "short-strangle-put",
                    position="sell",
                    option_type="put",
                    role="short_put",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=2,
                ),
            ],
        ),
        _base(
            now,
            name="Iron condor",
            description="Sell an OTM call and put with wider protective wings for a capped-risk range trade.",
            category="defined_risk_premium_selling",
            outlook="wide_sideways",
            expiry_policy="same_day",
            holding_mode="hold_to_expiry",
            risk_basis="defined_max_loss",
            risk_mode="combined_premium",
            legs=[
                _leg(
                    "condor-protective-put",
                    position="buy",
                    option_type="put",
                    role="protective_put",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=4,
                ),
                _leg(
                    "condor-short-put",
                    position="sell",
                    option_type="put",
                    role="short_put",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=2,
                ),
                _leg(
                    "condor-short-call",
                    position="sell",
                    option_type="call",
                    role="short_call",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=2,
                ),
                _leg(
                    "condor-protective-call",
                    position="buy",
                    option_type="call",
                    role="protective_call",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=4,
                ),
            ],
        ),
        _base(
            now,
            name="Iron butterfly",
            description="Sell the ATM call and put with equal OTM wings when BTC is expected to stay close to ATM.",
            category="defined_risk_premium_selling",
            outlook="tight_sideways",
            expiry_policy="same_day",
            holding_mode="hold_to_expiry",
            risk_basis="defined_max_loss",
            risk_mode="combined_premium",
            legs=[
                _leg("butterfly-short-call", position="sell", option_type="call", role="short_call", expiry=same_day),
                _leg("butterfly-short-put", position="sell", option_type="put", role="short_put", expiry=same_day),
                _leg(
                    "butterfly-protective-call",
                    position="buy",
                    option_type="call",
                    role="protective_call",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=3,
                ),
                _leg(
                    "butterfly-protective-put",
                    position="buy",
                    option_type="put",
                    role="protective_put",
                    expiry=same_day,
                    strike_mode="otm",
                    strike_steps=3,
                ),
            ],
        ),
    ]

    return [StrategyDefinition.model_validate(definition) for definition in definitions]
