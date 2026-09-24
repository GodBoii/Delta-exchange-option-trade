from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.default_strategies import default_strategy_definitions
from automation_agent.tools import materialize_live_definition

ACTIVATION = datetime(2026, 9, 21, 0, tzinfo=UTC)
EXPIRY = ACTIVATION.replace(hour=12) + timedelta(days=1)


def materialize(**kwargs):
    definition = default_strategy_definitions(ACTIVATION)[0].model_dump(mode="json", exclude_none=True)
    original = deepcopy(definition)
    result = materialize_live_definition(
        definition, activation=ACTIVATION,
        option_context={"options": [{"expiry": EXPIRY.isoformat()}]},
        expiry_policy="next_day", **kwargs,
    )
    assert definition == original
    assert result[0]["takeProfitPercent"] == 50
    assert all(leg["orderType"] == "market_order" for leg in result[0]["legs"])
    return result


@pytest.mark.parametrize("policy,hours,strategy_type", [
    ("intraday", 10, "intraday"), ("overnight", 26, "btst"), ("positional", 30, "positional"),
])
def test_agent_selects_holds_longer_than_seven_hours(policy, hours, strategy_type):
    desired = ACTIVATION + timedelta(hours=hours)
    live, exit_at = materialize(holding_policy=policy, planned_exit_time=desired.isoformat())
    assert exit_at == desired
    assert live["entry"]["strategyType"] == strategy_type


def test_expiry_hold_uses_buffer_without_seven_hour_cap():
    live, exit_at = materialize(holding_policy="hold_to_expiry")
    assert exit_at == EXPIRY - timedelta(minutes=5)
    assert live["holdingMode"] == "hold_to_expiry"
    assert live["entry"]["strategyType"] == "btst"


@pytest.mark.parametrize("kwargs", [
    {"holding_policy": "intraday"},
    {"holding_policy": "hold_to_expiry", "planned_exit_time": EXPIRY.isoformat()},
    {"holding_policy": "positional", "planned_exit_time": EXPIRY.isoformat()},
    {"holding_policy": "intraday", "planned_exit_time": (ACTIVATION + timedelta(hours=26)).isoformat()},
    {"holding_policy": "overnight", "planned_exit_time": (ACTIVATION + timedelta(hours=1)).isoformat()},
    {"holding_policy": "positional", "planned_exit_time": ACTIVATION.isoformat()},
    {"holding_policy": "positional", "planned_exit_time": "2026-09-21T08:00:00"},
])
def test_invalid_or_inconsistent_time_horizons_are_rejected(kwargs):
    with pytest.raises(ValueError):
        materialize(**kwargs)


def test_saved_policy_remains_backward_compatible():
    _, exit_at = materialize()
    assert exit_at == ACTIVATION + timedelta(hours=7)
