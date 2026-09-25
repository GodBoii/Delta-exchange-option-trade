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
    days = {"same_day": 1, "next_day": 1, "7_day": 7, "30_day": 30}[policy]
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
        "emergencyStopLossPercent": 170 if risk_basis != "net_debit" else None,
        "emergencyExitEnabled": True,
        "trailToBreakEven": False,
        "breakEvenScope": "all_legs",
        "lotsMode": "auto",
        "maximumLots": None,
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
                "Buy an at-the-money call when evidence favors a sustained BTC rise during the planned hold. "
                "The call gains value as BTC rises; its premium is the most that can be lost. Compare the likely "
                "price move with the quoted premium, time decay, and volatility before entry. A flat or falling "
                "market weakens the thesis."
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
                "Buy an at-the-money put when evidence favors a sustained BTC decline during the planned hold. "
                "The put gains value as BTC falls; its premium is the most that can be lost. Compare the likely "
                "price move with the quoted premium, time decay, and volatility before entry. A flat or rising "
                "market weakens the thesis."
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
                "Buy an at-the-money call and put with today's expiry when a sharp move is likely before expiry "
                "but its direction is unclear. Either leg can gain from a large move. The combined debit is at "
                "risk, and the move must outweigh both premiums, rapid same-day time decay, and any drop in "
                "implied volatility."
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
                "Buy a call and put two listed strikes out of the money when a large BTC move is likely but its "
                "direction is unclear. The pair costs less than an at-the-money straddle but needs a larger move "
                "to gain value. The combined debit is at risk; time decay and falling implied volatility work "
                "against both legs."
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
                "Sell the at-the-money call and put with today's expiry when BTC is likely to remain close to "
                "its current price until the expiry exit. Both premiums benefit from time decay. A strong move "
                "in either direction can overwhelm the credit; the uncovered shorts have substantial tail risk "
                "and monitored stops cannot guarantee a loss limit."
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
                "Sell a call and put two listed strikes out of the money with today's expiry when BTC is "
                "likely to remain between those strikes until the expiry exit. This collects less premium than "
                "an at-the-money straddle but allows a wider range. A breakout or volatility surge can erase "
                "the credit; both uncovered shorts have substantial tail risk."
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
    ]

    for option_type, outlook, boundary in (("put", "bullish", "support"), ("call", "bearish", "resistance")):
        definitions.append(
            _base(
                now,
                name=f"Short OTM {option_type}",
                description=(
                    f"Sell a {option_type} two listed strikes out of the money when BTC is expected to stay "
                    f"on the safe side of {boundary} during the planned hold. Time decay helps if the strike "
                    f"remains out of reach. A break through {boundary} or a volatility jump can quickly "
                    "outweigh the credit. This is an uncovered short option, and its stop cannot guarantee a "
                    "loss limit."
                ),
                category="premium_selling",
                outlook=outlook,
                expiry_policy="next_day",
                holding_mode="intraday",
                risk_basis="net_credit",
                risk_mode="strategy_level",
                legs=[
                    _leg(
                        f"short-otm-{option_type}",
                        position="sell",
                        option_type=option_type,
                        role=f"short_{option_type}",
                        expiry=_fallback_expiry(now, "next_day"),
                        strike_mode="otm",
                        strike_steps=2,
                    )
                ],
            )
        )

    for option_type, outlook in (("call", "bullish"), ("put", "bearish")):
        definitions.append(
            _base(
                now,
                name=f"Long ITM {option_type}",
                description=(
                    f"Buy a {option_type} two listed strikes in the money when an established {outlook} BTC "
                    "trend is likely to continue during the planned hold. The deeper strike gives more "
                    "directional exposure than an at-the-money option but costs more. A stalled or reversing "
                    "trend and time decay reduce its value; the full debit is at risk."
                ),
                category="premium_buying",
                outlook=outlook,
                expiry_policy="7_day",
                holding_mode="intraday",
                risk_basis="net_debit",
                risk_mode="strategy_level",
                legs=[
                    _leg(
                        f"long-itm-{option_type}",
                        position="buy",
                        option_type=option_type,
                        role=f"long_{option_type}",
                        expiry=seven_day,
                        strike_mode="itm",
                        strike_steps=2,
                    )
                ],
            )
        )

    existing = {definition["name"]: definition for definition in definitions}
    for source_name, description in (
        (
            "Long ATM straddle",
            "Buy an at-the-money call and put with the next listed expiry when a sharp BTC move is likely "
            "during the planned hold but direction is unclear. This expiry keeps both legs alive beyond "
            "today's settlement. The combined debit is at risk; the move must overcome both premiums, time "
            "decay, and any drop in implied volatility.",
        ),
        (
            "Short ATM straddle",
            "Sell the at-the-money call and put with the next listed expiry when BTC is likely to stay "
            "near its current price during the planned hold. This expiry covers a window beyond today's "
            "settlement. Time decay helps both legs, but a directional move or volatility jump can exceed "
            "the credit. Uncovered shorts carry substantial tail risk; stops cannot guarantee a loss limit.",
        ),
        (
            "Short strangle",
            "Sell a call and put two listed strikes out of the money with the next listed expiry when BTC "
            "is likely to stay between those strikes during the planned hold. This expiry covers a window "
            "beyond today's settlement. Time decay earns the credit while the range holds; a breakout or "
            "volatility surge can erase it. Both uncovered shorts carry substantial tail risk.",
        ),
    ):
        source = existing[source_name]
        definitions.append(
            {
                **source,
                "name": f"{source['name']} - next-day expiry",
                "description": description,
                "expiryPolicy": "next_day",
                "holdingMode": "intraday",
                "legs": [{**leg, "expiry": _fallback_expiry(now, "next_day")} for leg in source["legs"]],
            }
        )

    # Buy the farther wing first. Delta submits distinct option products one by one,
    # so an incomplete entry must not leave an uncovered short option behind.
    for option_type, outlook, boundary, name in (
        ("put", "bullish", "support", "Bull put credit spread"),
        ("call", "bearish", "resistance", "Bear call credit spread"),
    ):
        definitions.append(
            _base(
                now,
                name=name,
                description=(
                    f"Use for a mildly {outlook} BTC view when {boundary} is likely to hold during the "
                    f"planned hold. Buy a protective {option_type} three listed strikes out of the money, "
                    "then sell the nearer option one strike out at the same expiry. Time decay helps earn "
                    "the net credit. The long wing caps expiry loss, but the spread can still lose if BTC "
                    "crosses the short strike; fees and executable quotes matter."
                ),
                category="defined_risk_premium_selling",
                outlook=outlook,
                expiry_policy="next_day",
                holding_mode="intraday",
                risk_basis="defined_max_loss",
                risk_mode="strategy_level",
                legs=[
                    _leg(
                        f"{option_type}-credit-protection",
                        position="buy",
                        option_type=option_type,
                        role=f"protective_{option_type}",
                        expiry=_fallback_expiry(now, "next_day"),
                        strike_mode="otm",
                        strike_steps=3,
                    ),
                    _leg(
                        f"{option_type}-credit-short",
                        position="sell",
                        option_type=option_type,
                        role=f"short_{option_type}",
                        expiry=_fallback_expiry(now, "next_day"),
                        strike_mode="otm",
                        strike_steps=1,
                    ),
                ],
            )
        )

    return [StrategyDefinition.model_validate(definition) for definition in definitions]
