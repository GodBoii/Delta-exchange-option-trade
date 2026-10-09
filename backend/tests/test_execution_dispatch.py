import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine import TradingEngine
from app.errors import AppError


async def test_dispatch_bounds_parallel_accounts_and_serializes_exchange_aliases():
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())
    async def groups(_path, args):
        return [
            {"userId": user_id, "accountId": str(int(user_id.removeprefix("user-")) // 2)}
            for user_id in args["userIds"]
        ]
    engine.application_data = SimpleNamespace(request=AsyncMock(side_effect=groups))
    rows = [{"id": str(i), "user_id": f"user-{i}"} for i in range(250)]
    active = set()
    high_water = 0
    completed = []

    async def operation(row):
        nonlocal high_water
        account = int(row["id"]) // 2
        assert account not in active
        active.add(account)
        high_water = max(high_water, len(active))
        await asyncio.sleep(0)
        completed.append(row["id"])
        active.remove(account)

    await engine._dispatch_accounts(rows, operation)
    assert 1 < high_water <= 8
    assert len(completed) == len(set(completed)) == 250
    assert engine.application_data.request.await_count == 3
    await engine._dispatch_accounts(rows, operation)
    assert engine.application_data.request.await_count == 3
    engine.account_groups_refresh_at = 0
    await engine._dispatch_accounts(rows, operation)
    assert engine.application_data.request.await_count == 6


@pytest.mark.parametrize("free_slots", [0, 1, 2])
async def test_due_btc_entries_precede_earlier_eth_across_wallet_aliases(free_slots):
    now = datetime.now(UTC)
    rows = [
        {"id": "a-eth", "user_id": "alias", "asset": "ETH", "entry_at": (now - timedelta(seconds=60)).isoformat()},
        # Legacy BTC rows have no asset field.
        {"id": "z-btc", "user_id": "owner", "entry_at": (now - timedelta(seconds=1)).isoformat()},
    ]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace(max_entry_lateness_seconds=180))
    engine.application_data = SimpleNamespace(request=AsyncMock(return_value=[
        {"userId": "owner", "accountId": "wallet"}, {"userId": "alias", "accountId": "wallet"},
    ]))
    engine.strategy_pages = AsyncMock(side_effect=[rows, []])
    engine.activation_recheck_states = AsyncMock(return_value=dict.fromkeys([row["id"] for row in rows], "ready"))
    engine.process_active_risks = AsyncMock()
    engine.reconcile_attention_runs = AsyncMock()
    engine.reject_scheduled_entry = AsyncMock()
    attempted = []
    entered = []

    async def enter(strategy_id):
        attempted.append(strategy_id)
        if len(entered) >= free_slots:
            raise AppError(409, "All allocations occupied", "capital_slots_full")
        entered.append(strategy_id)

    engine.execute_entry = AsyncMock(side_effect=enter)
    try:
        await engine.process_due_strategies()
        assert attempted == ["z-btc", "a-eth"]
        assert entered == ["z-btc", "a-eth"][:free_slots]
        assert engine.reject_scheduled_entry.await_count == 2 - free_slots
    finally:
        await engine.close()
