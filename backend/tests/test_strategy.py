from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import StrategyDefinition, StrategyLeg
from app.strategy import combined_premium_metrics, resolve_leg

CHAIN = [
    item
    for strike in (60000, 61000, 62000, 63000)
    for item in (
        {
            "product_id": strike,
            "symbol": f"C-BTC-{strike}-080826",
            "contract_type": "call_options",
            "strike_price": str(strike),
            "spot_price": "61600",
            "mark_price": "100",
        },
        {
            "product_id": strike + 1,
            "symbol": f"P-BTC-{strike}-080826",
            "contract_type": "put_options",
            "strike_price": str(strike),
            "spot_price": "61600",
            "mark_price": "110",
        },
    )
]


def base_leg(**overrides):
    data = {
        "id": "leg-1",
        "lots": 2,
        "position": "buy",
        "optionType": "call",
        "expiry": "2026-08-08",
        "strikeMode": "atm",
        "strikeSteps": 0,
        "orderType": "market_order",
        "reentryOnTarget": 0,
        "reentryOnStop": 0,
    }
    return StrategyLeg.model_validate({**data, **overrides})


def base_strategy(**overrides):
    data = {
        "name": "BTC hedge",
        "instrument": {"index": "BTCUSD", "underlying": "BTC", "underlyingFrom": "cash"},
        "entry": {"strategyType": "intraday", "entryAt": "2026-08-08T04:00:00Z", "exitAt": "2026-08-08T10:00:00Z"},
        "squareOff": "complete",
        "trailToBreakEven": False,
        "breakEvenScope": "all_legs",
        "legs": [base_leg().model_dump(mode="json")],
        "acknowledgement": True,
    }
    return StrategyDefinition.model_validate({**data, **overrides})


def test_resolves_nearest_atm_strike():
    assert resolve_leg(base_leg(), CHAIN)["strike"] == 62000


def test_moves_calls_up_and_puts_down_for_otm():
    assert resolve_leg(base_leg(strikeMode="otm", strikeSteps=1), CHAIN)["strike"] == 63000
    assert resolve_leg(base_leg(optionType="put", strikeMode="otm", strikeSteps=1), CHAIN)["strike"] == 61000


def test_resolves_exact_strike():
    assert "60000" in resolve_leg(base_leg(strikeMode="exact", exactStrike=60000), CHAIN)["productSymbol"]


def test_accepts_valid_strategy():
    assert len(base_strategy().legs) == 1


def test_rejects_exit_before_entry():
    with pytest.raises(ValidationError):
        base_strategy(
            entry={"strategyType": "intraday", "entryAt": "2026-08-08T04:00:00Z", "exitAt": "2026-08-07T10:00:00Z"}
        )


def test_rejects_schedule_without_timezone():
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(
            base_strategy(
                entry={
                    "strategyType": "intraday",
                    "entryAt": "2026-08-08T04:00:00",
                    "exitAt": "2026-08-08T10:00:00",
                }
            )
        )


def test_requires_stop_loss_for_short_options():
    with pytest.raises(ValidationError):
        base_strategy(legs=[base_leg(position="sell").model_dump(mode="json")])


def test_accepts_combined_premium_short_straddle_without_leg_stops():
    strategy = base_strategy(
        riskMode="combined_premium",
        combinedStopLossPercent=100,
        emergencyStopLossPercent=300,
        legs=[
            base_leg(id="call", position="sell", optionType="call").model_dump(mode="json"),
            base_leg(id="put", position="sell", optionType="put").model_dump(mode="json"),
        ],
    )
    assert strategy.combinedStopLossPercent == 100
    assert all(leg.stopLoss is None for leg in strategy.legs)


def test_combined_premium_requires_two_short_legs():
    with pytest.raises(ValidationError):
        base_strategy(
            riskMode="combined_premium",
            combinedStopLossPercent=100,
            legs=[base_leg(position="sell").model_dump(mode="json")],
        )


def test_combined_premium_100_percent_triggers_at_twice_entry_credit():
    metrics = combined_premium_metrics(
        [
            {"side": "sell", "filled_size": 1, "entry_price": 120, "mark_price": 210, "contract_value": 1},
            {"side": "sell", "filled_size": 1, "entry_price": 80, "mark_price": 190, "contract_value": 1},
        ],
        Decimal("100"),
    )
    assert metrics["entry_credit"] == Decimal("200")
    assert metrics["close_cost"] == Decimal("400")
    assert metrics["loss"] == Decimal("200")
    assert metrics["trigger_close_cost"] == Decimal("400")
