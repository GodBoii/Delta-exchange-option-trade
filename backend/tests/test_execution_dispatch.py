import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.engine import TradingEngine


async def test_dispatch_bounds_parallel_accounts_and_serializes_exchange_aliases():
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace(convex_runtime_enabled=True))
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
