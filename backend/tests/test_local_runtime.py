"""Exercise indexed runtime reads and durable writes against disposable PostgreSQL."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.errors import AppError
from app.local_runtime import LocalRuntimeStore

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


@pytest.fixture(scope="module")
def event_loop_policy():
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
async def runtime():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        yield LocalRuntimeStore(pool)


@pytest.mark.asyncio
async def test_due_query_and_risk_only_updates_do_not_notify(runtime: LocalRuntimeStore):
    strategy_id = str(uuid4())
    user_id = str(uuid4())
    await runtime.write(
        "strategies",
        {
            "id": strategy_id,
            "user_id": user_id,
            "name": "Risk test",
            "status": "scheduled",
            "entry_at": "2026-09-24T12:00:00+00:00",
            "exit_at": "2026-09-24T13:00:00+00:00",
            "risk_state": {},
        },
    )
    due = await runtime.select(
        "strategies",
        {
            "select": "id,name,status",
            "status": "eq.scheduled",
            "entry_at": "lte.2026-09-24T12:01:00+00:00",
            "limit": "1",
        },
    )
    assert due == [{"id": strategy_id, "name": "Risk test", "status": "scheduled"}]
    async with await psycopg.AsyncConnection.connect(
        os.environ["TEST_LOCAL_DATABASE_URL"], autocommit=True
    ) as listener:
        await listener.execute("listen trade_changes")

        async def notices(limit: float = 0.5) -> list[dict]:
            found = []
            try:
                async with asyncio.timeout(limit):
                    async for notice in listener.notifies():
                        found.append(json.loads(notice.payload))
            except TimeoutError:
                pass
            return found

        await runtime.update("strategies", {"status": "active"}, {"id": f"eq.{strategy_id}"})
        assert {"table": "strategies", "owner": user_id} in await notices()
        # Risk-display refreshes run every few seconds per strategy; they must not wake browsers.
        await runtime.update("strategies", {"risk_state": {"profit": "1"}}, {"id": f"eq.{strategy_id}"})
        assert [item for item in await notices() if item["owner"] == user_id] == []
    with pytest.raises(AppError):
        await runtime.update("strategies", {}, {"id": f"eq.{strategy_id}"}, remove=True)


@pytest.mark.asyncio
async def test_capital_reservation_and_agent_claim_are_atomic(runtime: LocalRuntimeStore):
    user_id = str(uuid4())
    strategy_id = str(uuid4())
    connection = {"status": "connected", "delta_user_id": str(uuid4()), "ciphertext": "encrypted"}
    async with runtime.pool.connection() as db:
        await db.execute(
            """insert into trade.users (user_id,connection,automation,capital,record)
               values (%s,%s,%s,%s,%s)""",
            (user_id, Jsonb(connection), Jsonb({"enabled": True}), Jsonb({"allocation_mode": "half_balance"}),
             Jsonb({"userId": user_id})),
        )
    await runtime.write("strategies", {"id": strategy_id, "user_id": user_id, "status": "scheduled"})
    args = {
        "p_user_id": user_id, "p_strategy_id": strategy_id,
        "p_maximum_slots": 2, "p_budget": "50", "p_total_balance": "100",
    }
    assert await runtime.rpc("reserve_strategy_capital_slot", args) == {
        "slot": 1, "created": True, "occupiedBefore": 0,
    }
    assert await runtime.rpc("reserve_strategy_capital_slot", args) == {
        "slot": 1, "created": False, "occupiedBefore": 0,
    }
    assert await runtime.rpc("release_strategy_capital_slot", {
        "p_user_id": user_id, "p_strategy_id": strategy_id,
    }) is True
    run_id = str(uuid4())
    await runtime.write("automation_agent_runs", {
        "id": run_id, "user_id": user_id, "run_key": f"run:{run_id}",
        "trigger": "manual", "scheduled_for": "2026-09-24T12:00:00+00:00",
    })
    claimed = await runtime.rpc("claim_automation_agent_run", {"p_user_id": user_id, "p_run_id": run_id})
    assert len(claimed) == 1 and claimed[0]["status"] == "running"
    assert await runtime.rpc("claim_automation_agent_run", {"p_user_id": user_id, "p_run_id": run_id}) == []


@pytest.mark.asyncio
async def test_completed_strategy_releases_its_capital_slot(runtime: LocalRuntimeStore):
    user_id, strategy_id = str(uuid4()), str(uuid4())
    async with runtime.pool.connection() as connection:
        await connection.execute(
            """insert into trade.users (user_id,connection,automation,capital,record)
               values (%s,%s,%s,%s,%s)""",
            (user_id, Jsonb({"status": "connected", "delta_user_id": str(uuid4())}),
             Jsonb({"enabled": False}), Jsonb({"allocation_mode": "half_balance"}),
             Jsonb({"userId": user_id})),
        )
    await runtime.write("strategies", {"id": strategy_id, "user_id": user_id, "status": "active"})
    reservation = await runtime.rpc("reserve_strategy_capital_slot", {
        "p_user_id": user_id, "p_strategy_id": strategy_id,
        "p_maximum_slots": 1, "p_budget": "50", "p_total_balance": "100",
    })
    assert reservation["created"] is True
    await runtime.update("strategies", {"status": "completed"}, {"id": f"eq.{strategy_id}"})
    slots = await runtime.select("strategy_capital_slots", {"user_id": f"eq.{user_id}"})
    assert [(slot["status"], slot["strategy_id"]) for slot in slots] == [("available", None)]


@pytest.mark.asyncio
async def test_redundant_followups_are_cancelled_per_owner(runtime: LocalRuntimeStore):
    first, second = str(uuid4()), str(uuid4())
    runs = {}
    for owner, trigger, minutes in (
        (first, "agent_follow_up", 30), (first, "london_session", 20),
        (second, "agent_follow_up", 30), (second, "london_session", 40),
    ):
        run_id = str(uuid4())
        runs[(owner, trigger)] = run_id
        scheduled = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
        await runtime.write("automation_agent_runs", {
            "id": run_id, "user_id": owner, "trigger": trigger, "run_key": f"{trigger}:{run_id}",
            "scheduled_for": scheduled,
        })
    assert await runtime.rpc("cancel_redundant_automation_followups", {}) >= 1
    status = {
        key: (await runtime.select("automation_agent_runs", {"id": f"eq.{run_id}"}))[0]["status"]
        for key, run_id in runs.items()
    }
    assert status[(first, "agent_follow_up")] == "cancelled"
    assert status[(second, "agent_follow_up")] == "scheduled"
    assert status[(first, "london_session")] == status[(second, "london_session")] == "scheduled"
