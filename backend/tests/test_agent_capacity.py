import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import automation
from app.capital import CapitalPolicy, has_available_capital_slot
from app.engine import TradingEngine
from app.errors import AppError
from app.shared_analysis import SHARED_USER_ID


@pytest.mark.parametrize(
    "mode,maximum",
    [("full_balance", 1), ("half_balance", 2), ("one_third_balance", 3), ("one_quarter_balance", 4)],
)
def test_percentage_slots_allow_last_entry_then_block(mode, maximum):
    policy = CapitalPolicy(mode)
    budget = Decimal("120") / maximum
    assert has_available_capital_slot(
        budget, Decimal("120"), policy, list(range(1, maximum)), budget * (maximum - 1)
    )
    assert not has_available_capital_slot(
        budget, Decimal("120"), policy, list(range(1, maximum + 1)), Decimal("120")
    )


@pytest.mark.parametrize(
    "available,total,amount,slots,reserved,expected",
    [
        (25, 100, 25, [1, 2, 3], 75, True),
        (25, 100, 25, [1, 2, 3, 4], 100, False),
        (10, 100, 25, [1], 90, True),
        (50, 100, 200, [], 0, True),
        (0, 100, 25, [], 0, False),
        (25, 0, 25, [], 0, False),
        (25, 100, 25, [], 80, False),
        (25, 100, 25, [7, 8, 9, 10], 40, False),
        (1, 1000, 1, list(range(1, 101)), 100, False),
    ],
)
def test_fixed_budget_uses_existing_entry_limits(available, total, amount, slots, reserved, expected):
    assert has_available_capital_slot(
        Decimal(available), Decimal(total), CapitalPolicy("fixed_amount", Decimal(amount)), slots, Decimal(reserved)
    ) is expected


def test_released_slot_becomes_available_and_alias_budget_still_counts():
    policy = CapitalPolicy("one_quarter_balance")
    assert has_available_capital_slot(Decimal("25"), Decimal("100"), policy, [1, 3, 4], Decimal("75"))
    assert not has_available_capital_slot(Decimal("25"), Decimal("100"), policy, [1], Decimal("100"))


def test_existing_allocations_can_block_a_changed_capital_policy():
    assert not has_available_capital_slot(
        Decimal("100"), Decimal("100"), CapitalPolicy("half_balance"), [4], Decimal("75")
    )


@pytest.mark.parametrize("connected,slots", [(False, []), (True, [1, 2, 3, 4])])
async def test_full_or_disconnected_account_does_not_fetch_exchange_wallet(connected, slots):
    db = SimpleNamespace(rpc=AsyncMock(return_value={
        "connected": connected, "occupiedSlots": slots, "reservedBudget": "100",
    }))
    engine = TradingEngine(db, SimpleNamespace())
    engine.capital_policy = AsyncMock(return_value=CapitalPolicy("one_quarter_balance"))
    engine.client_for_user = AsyncMock()
    try:
        assert not await engine.has_available_trade_slot("user")
        db.rpc.assert_awaited_once_with("get_strategy_capital_capacity", {"p_user_id": "user"})
        engine.client_for_user.assert_not_awaited()
    finally:
        await engine.close()


@pytest.mark.parametrize("available,reserved,expected", [(25, 75, True), (0, 75, False), (25, 100, False)])
async def test_engine_combines_live_wallet_with_reserved_capital(available, reserved, expected):
    db = SimpleNamespace(rpc=AsyncMock(return_value={
        "connected": True, "occupiedSlots": [1, 2, 3], "reservedBudget": str(reserved),
    }))
    client = SimpleNamespace(close=AsyncMock())
    engine = TradingEngine(db, SimpleNamespace())
    engine.capital_policy = AsyncMock(return_value=CapitalPolicy("one_quarter_balance"))
    engine.client_for_user = AsyncMock(return_value=client)
    engine.usd_capital = AsyncMock(return_value=(Decimal(available), Decimal("100")))
    try:
        assert await engine.has_available_trade_slot("user") is expected
        client.close.assert_awaited_once()
        assert db.rpc.await_count == 1
    finally:
        await engine.close()


async def test_wallet_error_propagates_and_releases_the_client():
    db = SimpleNamespace(rpc=AsyncMock(return_value={
        "connected": True, "occupiedSlots": [], "reservedBudget": "0",
    }))
    client = SimpleNamespace(close=AsyncMock())
    engine = TradingEngine(db, SimpleNamespace())
    engine.capital_policy = AsyncMock(return_value=CapitalPolicy("fixed_amount", Decimal("25")))
    engine.client_for_user = AsyncMock(return_value=client)
    engine.usd_capital = AsyncMock(side_effect=TimeoutError("Wallet unavailable"))
    try:
        with pytest.raises(TimeoutError, match="Wallet unavailable"):
            await engine.has_available_trade_slot("user")
        client.close.assert_awaited_once()
    finally:
        await engine.close()


@pytest.mark.parametrize("availability,expected", [([], False), ([False, False], False), ([False, True], True)])
async def test_shared_review_requires_one_enabled_account_with_capacity(availability, expected):
    db = SimpleNamespace(select=AsyncMock(return_value=[{"user_id": str(i)} for i in range(len(availability))]))
    engine = SimpleNamespace(has_available_trade_slot=AsyncMock(side_effect=availability))
    assert await automation.has_automation_trade_capacity(db, engine, SHARED_USER_ID) is expected
    db.select.assert_awaited_once_with(
        "automation_settings", {"select": "user_id", "enabled": "eq.true", "user_id": "neq.global"}
    )


@pytest.mark.parametrize("other_capacity", [True, False])
async def test_unknown_account_capacity_does_not_block_another_account_with_room(other_capacity):
    db = SimpleNamespace(select=AsyncMock(return_value=[{"user_id": "offline"}, {"user_id": "other"}]))
    engine = SimpleNamespace(has_available_trade_slot=AsyncMock(side_effect=[TimeoutError("wallet"), other_capacity]))
    if other_capacity:
        assert await automation.has_automation_trade_capacity(db, engine, SHARED_USER_ID)
    else:
        with pytest.raises(AppError) as caught:
            await automation.has_automation_trade_capacity(db, engine, SHARED_USER_ID)
        assert caught.value.code == "capital_capacity_unavailable"


class CapacityRunDatabase:
    def __init__(self, user_id, trigger):
        self.settings = SimpleNamespace(shared_analysis_enabled=user_id == SHARED_USER_ID)
        self.row = {
            "id": "run", "user_id": user_id, "status": "scheduled", "trigger": trigger,
            "scheduled_for": datetime.now(UTC).isoformat(), "strategy_proposal_id": "proposal", "asset": "ETH",
        }
        self.updates = []
        self.claims = []

    async def select(self, table, params):
        if table == "automation_settings":
            return [{"user_id": SHARED_USER_ID, "enabled": True}] if params.get("user_id") == "eq.global" else [
                {"user_id": "account", "enabled": True}
            ]
        assert table == "automation_agent_runs"
        is_recheck = self.row["trigger"] == "activation_recheck"
        return [self.row.copy()] if self.row["status"] == "scheduled" and (
            params.get("trigger") == ("eq.activation_recheck" if is_recheck else "neq.activation_recheck")
        ) else []

    async def update(self, table, values, params):
        self.updates.append((table, values, params))
        if self.row["status"] == params.get("status", "").removeprefix("eq.") and "started_at" not in params:
            self.row.update(values)
            return [self.row.copy()]
        return []

    async def rpc(self, name, args):
        assert name == "claim_automation_agent_run"
        self.claims.append(args)
        self.row["status"] = "running"
        return [self.row.copy()]


@pytest.mark.parametrize("user_id", ["account", SHARED_USER_ID])
@pytest.mark.parametrize("trigger", ["asia_session", "london_session", "new_york_session", "pre_expiry",
                                     "midnight_review", "agent_follow_up", "manual", "activation_recheck"])
async def test_full_capacity_skips_due_reviews_before_readiness_or_claim(monkeypatch, user_id, trigger):
    db = CapacityRunDatabase(user_id, trigger)
    engine = SimpleNamespace(has_available_trade_slot=AsyncMock(return_value=False))
    ready = AsyncMock()
    monkeypatch.setattr(automation, "require_analyzer_ready", ready)
    scheduler = automation.AutomationScheduler(db, engine)
    scheduler._execute = AsyncMock()
    await scheduler._process_due_runs()
    assert db.row["status"] == "cancelled"
    assert db.row["error"] == automation.NO_TRADE_SLOTS_MESSAGE
    assert automation.public_run_error(db.row["error"]) == automation.NO_TRADE_SLOTS_MESSAGE
    assert db.row["completed_at"]
    assert db.claims == []
    assert not scheduler.running_tasks
    ready.assert_not_awaited()
    scheduler._execute.assert_not_awaited()
    assert {table for table, _, _ in db.updates} == {"automation_agent_runs"}
    # Polling does not retry the cancelled review once capacity changes.
    engine.has_available_trade_slot.return_value = True
    await scheduler._process_due_runs()
    assert db.claims == []


@pytest.mark.parametrize("user_id", ["account", SHARED_USER_ID])
async def test_new_review_can_start_after_a_slot_is_freed(monkeypatch, user_id):
    db = CapacityRunDatabase(user_id, "agent_follow_up")
    engine = SimpleNamespace(has_available_trade_slot=AsyncMock(return_value=True))
    ready = AsyncMock()
    monkeypatch.setattr(automation, "require_analyzer_ready", ready)
    scheduler = automation.AutomationScheduler(db, engine)
    scheduler._execute = AsyncMock()
    await scheduler._process_due_runs()
    await asyncio.gather(*scheduler.running_tasks)
    assert len(db.claims) == 1
    ready.assert_awaited_once()
    scheduler._execute.assert_awaited_once()


async def test_capacity_lookup_failure_defers_scheduled_review(monkeypatch):
    db = CapacityRunDatabase("account", "asia_session")
    engine = SimpleNamespace(has_available_trade_slot=AsyncMock(side_effect=TimeoutError("wallet")))
    ready = AsyncMock()
    monkeypatch.setattr(automation, "require_analyzer_ready", ready)
    scheduler = automation.AutomationScheduler(db, engine)
    await scheduler._process_due_runs()
    assert db.row["status"] == "scheduled"
    assert db.claims == []
    ready.assert_not_awaited()


@pytest.mark.parametrize("user_id", ["account", SHARED_USER_ID])
async def test_manual_review_with_no_capacity_does_not_create_a_run(monkeypatch, user_id):
    db = SimpleNamespace(
        settings=SimpleNamespace(shared_analysis_enabled=user_id == SHARED_USER_ID),
        profile=AsyncMock(return_value={"user_type": "owner"}), insert=AsyncMock(),
        select=AsyncMock(return_value=[{"user_id": "account"}]),
        runtime=SimpleNamespace(data=SimpleNamespace(request=AsyncMock())),
    )
    engine = SimpleNamespace(has_available_trade_slot=AsyncMock(return_value=False))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, engine=engine)))
    ready = AsyncMock()
    monkeypatch.setattr(automation, "require_analyzer_ready", ready)
    monkeypatch.setattr(automation, "current_account", AsyncMock())
    monkeypatch.setattr(automation, "ensure_settings", AsyncMock(return_value={"enabled": True}))
    with pytest.raises(AppError) as caught:
        await automation.run_automation(request, automation.AutomationRunRequest(), {"id": "account"})
    assert caught.value.code == "capital_slots_full"
    assert caught.value.status == 409
    db.insert.assert_not_awaited()
    db.runtime.data.request.assert_not_awaited()
    ready.assert_not_awaited()


@pytest.mark.parametrize("trigger", ["manual", "activation_recheck"])
async def test_capacity_is_checked_again_before_analyzer_request(monkeypatch, trigger):
    db = CapacityRunDatabase("account", trigger)
    db.row["status"] = "running"
    engine = SimpleNamespace(has_available_trade_slot=AsyncMock(return_value=False))
    context = AsyncMock(side_effect=AssertionError("No context is needed for a skipped run"))
    monkeypatch.setattr(automation, "build_account_context", context)
    monkeypatch.setattr(automation, "build_activation_recheck_context", context)
    monkeypatch.setattr(automation.httpx, "AsyncClient", lambda **_: pytest.fail("Analyzer must not be called"))
    result = await automation.execute_automation_run(
        db=db, engine=engine, user_id="account", run_id="run", session_id="session", trigger=trigger,
        reason="review", strategy_proposal_id="proposal",
    )
    assert result["skipped"] is True
    assert result["status"] == db.row["status"] == "cancelled"
    assert not db.row.get("outcome")
    context.assert_not_awaited()
