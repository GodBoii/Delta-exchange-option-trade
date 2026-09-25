"""Exercise indexed runtime reads and durable writes against disposable PostgreSQL."""

import asyncio
import os
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.errors import AppError
from app.local_journal import LocalOrderJournal
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
async def test_due_query_and_risk_only_mirror_skip(runtime: LocalRuntimeStore):
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
    await runtime.update("strategies", {"status": "active"}, {"id": f"eq.{strategy_id}"})
    async with runtime.pool.connection() as connection:
        initial = await connection.execute(
            "select count(*) from trade.recovery_outbox where entity_type='strategies' and entity_key=%s",
            (strategy_id,),
        )
        assert (await initial.fetchone())[0] == 2
    await runtime.update("strategies", {"risk_state": {"profit": "1"}}, {"id": f"eq.{strategy_id}"})
    async with runtime.pool.connection() as connection:
        after = await connection.execute(
            "select count(*) from trade.recovery_outbox where entity_type='strategies' and entity_key=%s",
            (strategy_id,),
        )
        assert (await after.fetchone())[0] == 2
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
async def test_completed_strategy_marks_order_recovery_records_closed(runtime: LocalRuntimeStore):
    user_id, strategy_id, execution_id, order_id = (str(uuid4()) for _ in range(4))
    client_order_id = f"ord_{uuid4().hex[:16]}"
    delta_account = str(uuid4())
    account_id = f"india:{delta_account}"
    async with runtime.pool.connection() as connection:
        await connection.execute(
            """insert into trade.users (user_id,connection,automation,capital,record)
               values (%s,%s,%s,%s,%s)""",
            (user_id, Jsonb({"status": "connected", "delta_user_id": delta_account}),
             Jsonb({"enabled": False}), Jsonb({"allocation_mode": "half_balance"}),
             Jsonb({"userId": user_id})),
        )
    await runtime.write("strategies", {"id": strategy_id, "user_id": user_id, "status": "active"})
    await runtime.write("executions", {
        "id": execution_id, "strategy_id": strategy_id, "status": "completed", "kind": "entry",
    })
    await runtime.write("execution_orders", {
        "id": order_id, "execution_id": execution_id, "state": "settled",
        "client_order_id": client_order_id, "delta_order_id": "exchange_closed",
    })
    journal = LocalOrderJournal(runtime.pool, account_id)
    await journal.call("orderIntents:begin", {
        "clientOrderId": client_order_id, "payload": '{}', "context": {"strategyId": strategy_id},
    })
    await journal.call("orderIntents:resolve", {
        "clientOrderId": client_order_id, "outcome": {"kind": "accepted", "response": '{"result":{"id":1}}'},
    })
    await journal.call("exchangeFills:ingest", {"fills": [{
        "fillId": "fill_closed", "productId": "101", "orderId": "exchange_closed",
        "side": "buy", "quantity": "1", "price": "10", "commission": None,
        "occurredAt": "2026-09-24T12:00:00+00:00",
    }]})
    await runtime.update("strategies", {"status": "completed"}, {"id": f"eq.{strategy_id}"})
    async with runtime.pool.connection() as connection:
        for table, key in (("executions", execution_id), ("execution_orders", order_id)):
            result = await connection.execute(
                f"select data->>'recovery_closed_at' from trade.{table} where id=%s", (key,)
            )
            assert (await result.fetchone())[0]
        intent = await connection.execute(
            "select recovery_closed_at from trade.order_intents where account_id=%s and client_order_id=%s",
            (account_id, client_order_id),
        )
        fill = await connection.execute(
            "select recovery_closed_at from trade.exchange_fills where account_id=%s and fill_id=%s",
            (account_id, "fill_closed"),
        )
        assert (await intent.fetchone())[0] is not None
        assert (await fill.fetchone())[0] is not None
