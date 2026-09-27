"""Switching automation off stops new agent entries and leaves live positions alone."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app import automation
from app import engine as engine_module
from app.engine import TERMINAL_SCHEDULED_ENTRY_CODES, TradingEngine
from app.errors import AppError


class Db:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.updates: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        if table == "strategies":
            return [{"id": "run", "user_id": "user", "status": "scheduled", "saved_strategy_id": "saved",
                     "definition_json": {}}]
        if table == "strategy_proposals":
            if params.get("status") == "eq.scheduled":
                return [{"id": "proposal", "strategy_id": "run"}]
            return [{"saved_strategy_version": 1}]
        if table == "automation_settings":
            return [{"user_id": "user"}] if self.enabled else []
        raise AssertionError(table)

    async def update(self, table: str, payload: dict[str, Any], params: dict[str, str]) -> list[dict[str, Any]]:
        self.updates.append((table, payload, params))
        return []


async def test_agent_proposal_does_not_enter_after_automation_is_switched_off(monkeypatch):
    monkeypatch.setattr(engine_module, "StrategyDefinition", SimpleNamespace(model_validate=lambda value: value))
    engine = TradingEngine.__new__(TradingEngine)
    engine.db = Db(enabled=False)
    engine.startup_recovered = True
    engine.client_for_user = AsyncMock()
    with pytest.raises(AppError) as caught:
        await TradingEngine._execute_entry(engine, "run")
    assert caught.value.code == "automation_disabled"
    assert "automation_disabled" in TERMINAL_SCHEDULED_ENTRY_CODES
    engine.client_for_user.assert_not_awaited()


async def test_switch_off_cancels_only_scheduled_work():
    db = Db(enabled=True)
    db.local_data = SimpleNamespace(set_automation_enabled=AsyncMock(return_value=(True, {"enabled": False})))
    saved = await automation.set_account_automation(db, "user", False, actor_id="owner")
    assert saved == {"enabled": False}
    db.local_data.set_automation_enabled.assert_awaited_once_with(
        "user", False, model_id=automation.MODEL_ID, actor_id="owner"
    )
    # Every cancellation is conditional on status=scheduled, so active runs and positions are untouched.
    assert db.updates and all(params.get("status") == "eq.scheduled" for _, _, params in db.updates)
    assert {table for table, _, _ in db.updates} == {"automation_agent_runs", "strategies", "strategy_proposals"}


async def test_switch_on_does_not_cancel_anything():
    db = Db(enabled=True)
    db.local_data = SimpleNamespace(set_automation_enabled=AsyncMock(return_value=(False, {"enabled": True})))
    await automation.set_account_automation(db, "user", True)
    assert db.updates == []
