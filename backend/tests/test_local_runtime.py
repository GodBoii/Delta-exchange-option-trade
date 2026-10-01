"""Exercise indexed runtime reads and durable writes against disposable PostgreSQL."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app import automation
from app.automation import NO_TRADE_SLOTS_MESSAGE, execute_automation_run
from app.database import Database
from app.engine import TradingEngine
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
async def test_capacity_counts_both_assets_and_alias_wallet_budgets(runtime: LocalRuntimeStore):
    user_id, alias_id, other_id = (str(uuid4()) for _ in range(3))
    account_id = str(uuid4())
    async with runtime.pool.connection() as connection:
        for owner, wallet_id in ((user_id, account_id), (alias_id, account_id), (other_id, str(uuid4()))):
            await connection.execute(
                """insert into trade.users (user_id,connection,automation,capital,record)
                   values (%s,%s,%s,%s,%s)""",
                (owner, Jsonb({"status": "connected", "delta_user_id": wallet_id}),
                 Jsonb({"enabled": True}), Jsonb({"allocation_mode": "one_quarter_balance"}), Jsonb({})),
            )
    strategies = []
    for owner, asset, budget in ((user_id, "BTC", "25"), (user_id, "ETH", "25"),
                                 (alias_id, "BTC", "50"), (other_id, "BTC", "100")):
        strategy_id = str(uuid4())
        strategies.append(strategy_id)
        await runtime.write("strategies", {"id": strategy_id, "user_id": owner, "asset": asset, "status": "active"})
        await runtime.rpc("reserve_strategy_capital_slot", {
            "p_user_id": owner, "p_strategy_id": strategy_id, "p_maximum_slots": 4,
            "p_budget": budget, "p_total_balance": "100",
        })
    await runtime.update("strategy_capital_slots", {"status": "active"}, {"user_id": f"eq.{alias_id}"})
    args = {"p_user_id": user_id}
    before = await runtime.select("strategy_capital_slots", {"user_id": f"eq.{user_id}"})
    capacity = await runtime.rpc("get_strategy_capital_capacity", args)
    assert capacity == {"connected": True, "occupiedSlots": [1, 2], "reservedBudget": "100"}
    assert await runtime.select("strategy_capital_slots", {"user_id": f"eq.{user_id}"}) == before
    await runtime.update("strategies", {"status": "completed"}, {"id": f"eq.{strategies[0]}"})
    assert await runtime.rpc("get_strategy_capital_capacity", args) == {
        "connected": True, "occupiedSlots": [2], "reservedBudget": "75",
    }
    assert await runtime.rpc("get_strategy_capital_capacity", {"p_user_id": str(uuid4())}) == {
        "connected": False, "occupiedSlots": [], "reservedBudget": "0",
    }


@pytest.mark.asyncio
async def test_four_quarter_allocations_skip_analysis_and_release_restores_capacity(runtime, monkeypatch):
    user_id = str(uuid4())
    async with runtime.pool.connection() as connection:
        await connection.execute(
            """insert into trade.users (user_id,connection,automation,capital,record) values (%s,%s,%s,%s,%s)""",
            (user_id, Jsonb({"status": "connected", "delta_user_id": str(uuid4())}),
             Jsonb({"enabled": True}), Jsonb({"allocation_mode": "one_quarter_balance"}), Jsonb({})),
        )
    strategy_ids = [str(uuid4()) for _ in range(5)]
    for index, strategy_id in enumerate(strategy_ids):
        await runtime.write("strategies", {
            "id": strategy_id, "user_id": user_id, "status": "active" if index < 4 else "scheduled",
            "asset": "BTC" if index % 2 else "ETH",
        })
        if index < 4:
            await runtime.rpc("reserve_strategy_capital_slot", {
                "p_user_id": user_id, "p_strategy_id": strategy_id, "p_maximum_slots": 4,
                "p_budget": "25", "p_total_balance": "100",
            })
    settings = SimpleNamespace(
        supabase_url="https://supabase.test", supabase_publishable_key="test", supabase_service_role_key="test",
        analysis_service_secret="test-analysis-service-secret", chart_link_seconds=3600,
    )
    db = Database(settings, runtime.pool)
    engine = TradingEngine(db, SimpleNamespace())
    client = SimpleNamespace(close=AsyncMock())
    engine.client_for_user = AsyncMock(return_value=client)
    engine.usd_capital = AsyncMock(return_value=(Decimal("25"), Decimal("100")))
    try:
        assert not await engine.has_available_trade_slot(user_id)
        engine.client_for_user.assert_not_awaited()
        # Real run persistence also stores the skip report outside the runtime row.
        run_id = str(uuid4())
        await db.insert("automation_agent_runs", {
            "id": run_id, "user_id": user_id, "trigger": "manual", "status": "running",
            "scheduled_for": datetime.now(UTC).isoformat(),
        })
        monkeypatch.setattr(automation, "build_account_context", AsyncMock(side_effect=AssertionError("No analysis")))
        result = await execute_automation_run(
            db=db, engine=engine, user_id=user_id, run_id=run_id, session_id="capacity-test", trigger="manual",
            reason="No capacity",
        )
        assert result["skipped"]
        row = (await db.select("automation_agent_runs", {"id": f"eq.{run_id}"}))[0]
        assert row["status"] == "cancelled" and row["error"] == NO_TRADE_SLOTS_MESSAGE
        assert row["outcome"] is None and "skipped" in row["report_markdown"]
        with pytest.raises(AppError) as caught:
            await runtime.rpc("reserve_strategy_capital_slot", {
                "p_user_id": user_id, "p_strategy_id": strategy_ids[4], "p_maximum_slots": 4,
                "p_budget": "25", "p_total_balance": "100",
            })
        assert caught.value.code == "capital_slots_full"
        await db.update("strategies", {"status": "completed"}, {"id": f"eq.{strategy_ids[0]}"})
        assert await engine.has_available_trade_slot(user_id)
        # Analysis only reads capacity; the existing entry RPC still owns reservation.
        capacity = await runtime.rpc("get_strategy_capital_capacity", {"p_user_id": user_id})
        assert capacity["occupiedSlots"] == [2, 3, 4]
        reservation = await runtime.rpc("reserve_strategy_capital_slot", {
            "p_user_id": user_id, "p_strategy_id": strategy_ids[4], "p_maximum_slots": 4,
            "p_budget": "25", "p_total_balance": "100",
        })
        assert reservation["slot"] == 1
    finally:
        await engine.close()
        await db.close()


@pytest.mark.asyncio
async def test_btc_eth_and_recheck_runs_claim_while_another_run_is_running(runtime: LocalRuntimeStore):
    user_id = str(uuid4())
    runs = [
        {"id": str(uuid4()), "user_id": user_id, "asset": asset, "trigger": trigger,
         "run_key": f"{asset}:{trigger}:{uuid4()}", "scheduled_for": "2026-09-24T12:00:00+00:00"}
        for asset, trigger in (("BTC", "london_session"), ("ETH", "london_session"),
                               ("BTC", "activation_recheck"), ("ETH", "manual"))
    ]
    for run in runs:
        await runtime.write("automation_agent_runs", run)
    claimed = await asyncio.gather(*(
        runtime.rpc("claim_automation_agent_run", {"p_user_id": user_id, "p_run_id": run["id"]}) for run in runs
    ))
    assert [len(rows) for rows in claimed] == [1, 1, 1, 1]
    assert all(rows[0]["status"] == "running" for rows in claimed)


@pytest.mark.asyncio
async def test_fixed_btc_and_eth_reviews_coexist_and_only_cancel_their_own_follow_ups(runtime: LocalRuntimeStore):
    user_id = str(uuid4())
    fixed_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    runs = [
        {"user_id": user_id, "asset": asset, "run_key": key, "trigger": "london_session",
         "scheduled_for": fixed_at, "status": "scheduled"}
        for asset, key in (("BTC", "london_session:2099-01-01"), ("ETH", "ETH:london_session:2099-01-01"))
    ]
    assert await runtime.rpc("ensure_automation_fixed_runs", {"p_runs": runs}) == 2
    assert await runtime.rpc("ensure_automation_fixed_runs", {"p_runs": runs}) == 0
    follow_up = str(uuid4())
    await runtime.write("automation_agent_runs", {
        "id": follow_up, "user_id": user_id, "asset": "ETH", "trigger": "agent_follow_up",
        "run_key": f"ETH:follow-up:{uuid4()}", "status": "scheduled",
        "scheduled_for": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
    })
    await runtime.update(
        "automation_agent_runs", {"status": "cancelled"}, {"user_id": f"eq.{user_id}", "asset": "eq.ETH",
                                                            "trigger": "eq.london_session"},
    )
    await runtime.rpc("cancel_redundant_automation_followups", {})
    rows = await runtime.select("automation_agent_runs", {"id": f"eq.{follow_up}"})
    # Only the scheduled BTC fixed review precedes it, and BTC reviews never cancel ETH follow-ups.
    assert rows[0]["status"] == "scheduled"


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
