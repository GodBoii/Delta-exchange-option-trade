"""Retirement prevents new entries without removing historical definitions."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.default_strategies import builtin_strategy_definitions, is_retired_long_atm_straddle
from app.engine import TradingEngine
from app.errors import AppError
from app.local_research_operations import LocalResearchOperations
from automation_agent.tools import AutomationStrategyTools


@pytest.fixture
def retired_definition():
    source = next(item for item in builtin_strategy_definitions() if item.name == "Long strangle")
    definition = source.model_dump(mode="json", exclude_none=True)
    definition["name"] = "Renamed volatility trade"
    for leg in definition["legs"]:
        leg.update(strikeMode="atm", strikeSteps=0)
    return definition


def test_remaining_catalog_contains_no_retired_shapes():
    definitions = builtin_strategy_definitions()
    assert len(definitions) == 19
    assert all(not is_retired_long_atm_straddle(item.model_dump()) for item in definitions)
    assert {"Short ATM straddle", "Long strangle", "ETH Long strangle"} <= {item.name for item in definitions}


@pytest.mark.parametrize("name", [
    "Long ATM straddle", "ETH Long ATM straddle", "Long ATM straddle - next-day expiry",
    "ETH Long ATM straddle - next-day expiry", "  LONG ATM STRADDLE  ",
])
def test_legacy_names_are_retired(name):
    assert is_retired_long_atm_straddle({"name": name})


def test_renamed_atm_buy_pair_is_retired(retired_definition):
    assert is_retired_long_atm_straddle(retired_definition)
    retired_definition["legs"][0]["position"] = "sell"
    assert not is_retired_long_atm_straddle(retired_definition)


def test_atm_calendar_pair_is_not_a_straddle(retired_definition):
    retired_definition["legs"][1]["expiry"] = "2099-01-01"
    assert not is_retired_long_atm_straddle(retired_definition)


def test_agent_hides_retired_shape_and_rejects_stale_reference(retired_definition):
    tool = object.__new__(AutomationStrategyTools)
    tool.user_id, tool.asset, tool.agent_run_id = "global", "BTC", "run-1"
    tool.application_data = SimpleNamespace(selection_context=lambda *_: ([{
        "id": "legacy", "name": retired_definition["name"], "definition_json": retired_definition,
        "version": 8, "enabled_for_ai": True, "user_id": None,
    }], {"allocation_mode": "half_balance"}))
    tool.runtime_data = SimpleNamespace(request_sync=lambda *_: {"occupied": 0})
    assert json.loads(tool.show_available_strategy())["strategies"] == []
    assert tool.strategy_references == {}
    with pytest.raises(ValueError, match="unavailable"):
        tool._resolve_selection("legacy", 8, datetime.now(UTC), None)


async def test_backend_rejects_retired_proposal_even_if_template_reenabled(retired_definition):
    operations = object.__new__(LocalResearchOperations)
    cursor = SimpleNamespace(execute=AsyncMock(), fetchone=AsyncMock(return_value={
        "definition_json": retired_definition, "deleted": False, "enabled_for_ai": True,
        "version": 8, "user_id": None,
    }))
    with pytest.raises(AppError) as caught:
        await operations._saved(cursor, "legacy", 8, "global")
    assert caught.value.code == "saved_strategy_changed"


async def test_entry_rejects_retired_run_before_exchange_access(retired_definition):
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())
    engine.startup_recovered = True
    engine.strategy_by_id = AsyncMock(return_value={"definition_json": retired_definition})
    engine.client_for_run = AsyncMock()
    with pytest.raises(AppError) as caught:
        await engine.execute_entry("legacy-run")
    assert caught.value.code == "strategy_retired"
    engine.client_for_run.assert_not_awaited()
    assert not engine.running_operations
