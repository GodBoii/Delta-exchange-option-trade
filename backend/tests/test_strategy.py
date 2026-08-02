import pytest
from pydantic import ValidationError

from app.models import StrategyDefinition, StrategyLeg
from app.strategy import resolve_leg

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


def test_requires_stop_loss_for_short_options():
    with pytest.raises(ValidationError):
        base_leg(position="sell")
