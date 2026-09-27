"""Owner ledger capture, archive-on-delete, backfill and summaries against real PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

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


async def settled_run(runtime: LocalRuntimeStore, user_id: str, *, exit_price: str = "400", when=None) -> str:
    """A completed run: sell 1 lot at 1000, buy back at ``exit_price``, 0.1 fee per order."""
    run_id = str(uuid4())
    entered = (when or datetime.now(UTC)) - timedelta(hours=2)
    await runtime.write("strategies", {
        "id": run_id, "user_id": user_id, "name": "Short call", "status": "scheduled",
        "entry_at": entered.isoformat(), "exit_at": (entered + timedelta(hours=1)).isoformat(),
    })
    await runtime.update("strategies", {
        "status": "active", "entry_execution_at": entered.isoformat(), "capital_budget": "50",
        "capital_policy_json": {"allocationMode": "half_balance", "totalBalanceAtEntry": "100",
                                "availableBalanceAtEntry": "90"},
    }, {"id": f"eq.{run_id}"})
    for kind, side, price in (("entry", "sell", "1000"), ("exit", "buy", exit_price)):
        execution = (await runtime.write("executions", {"strategy_id": run_id, "kind": kind, "status": "completed"}))[0]
        await runtime.write("execution_orders", {
            "execution_id": execution["id"], "client_order_id": f"t_{uuid4().hex[:20]}", "side": side,
            "size": 1, "filled_size": "1", "average_fill_price": price, "contract_value": "0.001",
            "commission": "0.1", "state": "closed", "product_symbol": "C-BTC-1", "product_id": 1,
            "response_json": {"raw": "exchange body"},
        })
    exited = (entered + timedelta(hours=1)).isoformat()
    await runtime.update("strategies", {"status": "completed", "exit_execution_at": exited}, {"id": f"eq.{run_id}"})
    return run_id


async def ledger_row(runtime: LocalRuntimeStore, run_id: str):
    async with runtime.pool.connection() as connection:
        cursor = await connection.execute(
            """select accounting_state,realized_pnl,exchange_fees,deleted_by_user_at,detail,last_captured_at,
                      capital_budget,wallet_total_at_entry
               from owner_reporting.trade_ledger where run_id=%s""",
            (run_id,),
        )
        return await cursor.fetchone()


async def test_completed_run_is_captured_sanitized_with_entry_capital(runtime: LocalRuntimeStore):
    user_id = str(uuid4())
    run_id = await settled_run(runtime, user_id)
    state, realized, fees, deleted, detail, _, budget, wallet = await ledger_row(runtime, run_id)
    assert (state, realized, fees, deleted) == ("settled", Decimal("0.4"), Decimal("0.2"), None)
    assert (budget, wallet) == (Decimal("50"), Decimal("100"))
    assert len(detail["orders"]) == 2 and all("response" not in order for order in detail["orders"])
    history = await runtime.ledger.capital_history(user_id)
    assert {(item["kind"], item["source"]) for item in history} == {
        ("run_allocation", "strategy_entry"), ("wallet", "strategy_entry"), ("policy", "strategy_entry"),
    }
    wallet_row = next(item for item in history if item["kind"] == "wallet")
    assert wallet_row["total_balance"] == "100.0000000000" and wallet_row["run_id"] == run_id


async def test_risk_display_refresh_does_not_rewrite_the_copy(runtime: LocalRuntimeStore):
    run_id = await settled_run(runtime, str(uuid4()))
    before = (await ledger_row(runtime, run_id))[5]
    await asyncio.sleep(0.01)
    await runtime.update("strategies", {"risk_state": {"profit": "2"}}, {"id": f"eq.{run_id}"})
    assert (await ledger_row(runtime, run_id))[5] == before


async def test_user_delete_keeps_owner_copy_and_splits_personal_from_owner_totals(runtime: LocalRuntimeStore):
    user_id = str(uuid4())
    kept = await settled_run(runtime, user_id, exit_price="400")
    removed = await settled_run(runtime, user_id, exit_price="1500")
    engine = SimpleNamespace(db=SimpleNamespace(
        select=runtime.select, delete=lambda table, params: runtime.update(table, {}, params, remove=True),
    ))
    with pytest.raises(AppError) as foreign:
        await TradingEngine.delete_strategy(engine, removed, str(uuid4()))
    assert foreign.value.code == "strategy_not_found"
    await TradingEngine.delete_strategy(engine, removed, user_id)

    assert await runtime.select("strategies", {"id": f"eq.{removed}"}) == []
    async with runtime.pool.connection() as connection:
        orphans = await (await connection.execute(
            "select count(*) from trade.executions where relation_id=%s", (removed,)
        )).fetchone()
    assert orphans[0] == 0
    row = await ledger_row(runtime, removed)
    assert row[0] == "settled" and row[3] is not None and len(row[4]["orders"]) == 2

    personal = await runtime.ledger.summary(user_id, deleted="exclude", since=None)
    owner = await runtime.ledger.summary(user_id, deleted="include", since=None)
    assert personal["netRealizedPnl"] == "0.4000000000" and personal["settledRuns"] == 1
    assert owner["settledRuns"] == 2 and owner["deletedByUserRuns"] == 1
    assert Decimal(owner["netRealizedPnl"]) == Decimal("0.4") + Decimal("-0.7")
    assert (owner["wins"], owner["losses"], owner["winRate"]) == (1, 1, 0.5)
    visible = await runtime.ledger.trades(user_id, deleted="exclude", since=None, state=None, cursor=None, limit=10)
    assert [item["run_id"] for item in visible["items"]] == [kept]
    only_deleted = await runtime.ledger.trades(user_id, deleted="only", since=None, state=None, cursor=None, limit=10)
    assert [item["run_id"] for item in only_deleted["items"]] == [removed]


async def test_live_run_delete_is_refused_and_leaves_no_deletion_mark(runtime: LocalRuntimeStore):
    user_id = str(uuid4())
    run_id = str(uuid4())
    await runtime.write("strategies", {"id": run_id, "user_id": user_id, "name": "Live", "status": "active"})
    with pytest.raises(AppError):
        await runtime.update("strategies", {}, {"id": f"eq.{run_id}"}, remove=True)
    row = await ledger_row(runtime, run_id)
    assert row[0] == "open" and row[3] is None


async def test_archive_failure_rolls_back_the_deletion(runtime: LocalRuntimeStore, monkeypatch):
    user_id = str(uuid4())
    run_id = await settled_run(runtime, user_id)
    original = runtime.ledger.capture

    async def failing(connection, identifier, *, deleted=False):
        if deleted:
            raise RuntimeError("reporting table unavailable")
        return await original(connection, identifier, deleted=deleted)

    monkeypatch.setattr(runtime.ledger, "capture", failing)
    with pytest.raises(RuntimeError):
        await runtime.update("strategies", {}, {"id": f"eq.{run_id}"}, remove=True)
    assert len(await runtime.select("strategies", {"id": f"eq.{run_id}"})) == 1
    assert (await ledger_row(runtime, run_id))[3] is None


async def test_capture_failure_never_blocks_a_trading_write(runtime: LocalRuntimeStore, monkeypatch):
    async def broken(connection, identifier, *, deleted=False):
        raise RuntimeError("bug in reporting")

    monkeypatch.setattr(runtime.ledger, "capture", broken)
    run_id = str(uuid4())
    await runtime.write("strategies", {"id": run_id, "user_id": str(uuid4()), "name": "Run", "status": "scheduled"})
    assert len(await runtime.select("strategies", {"id": f"eq.{run_id}"})) == 1


async def test_backfill_is_idempotent_and_verifies_counts(runtime: LocalRuntimeStore):
    user_id, run_id = str(uuid4()), str(uuid4())
    # A historical run written before capture existed.
    async with runtime.pool.connection() as connection:
        await connection.execute(
            """insert into trade.strategies (id,owner_id,status,created_at,data)
               values (%s,%s,'completed',now(),%s)""",
            (run_id, user_id, Jsonb({"id": run_id, "user_id": user_id, "name": "Old", "status": "completed",
                                     "created_at": datetime.now(UTC).isoformat()})),
        )
    assert await ledger_row(runtime, run_id) is None
    first = await runtime.ledger.backfill()
    second = await runtime.ledger.backfill()
    assert first["verified"] and second["verified"]
    assert first["ledger_runs"] == second["ledger_runs"]
    row = await ledger_row(runtime, run_id)
    assert row[0] == "incomplete"
    assert (await runtime.ledger.history_status())["complete"] is True


async def test_trade_pages_use_a_stable_cursor(runtime: LocalRuntimeStore):
    user_id = str(uuid4())
    base = datetime.now(UTC)
    runs = [await settled_run(runtime, user_id, when=base - timedelta(days=index)) for index in range(3)]
    first = await runtime.ledger.trades(user_id, deleted="include", since=None, state="settled", cursor=None, limit=2)
    second = await runtime.ledger.trades(
        user_id, deleted="include", since=None, state="settled", cursor=first["nextCursor"], limit=2
    )
    assert [item["run_id"] for item in first["items"] + second["items"]] == runs
    assert second["nextCursor"] is None
    recent = await runtime.ledger.summary(user_id, deleted="include", since=base - timedelta(days=1, hours=12))
    assert recent["settledRuns"] == 2


async def test_owner_switch_off_is_audited_and_returns_saved_state(runtime: LocalRuntimeStore):
    target, actor = str(uuid4()), str(uuid4())
    previous, saved = await runtime.data.set_automation_enabled(target, True, model_id="m", actor_id=actor)
    assert previous is False and saved["enabled"] is True
    previous, saved = await runtime.data.set_automation_enabled(target, False, model_id="m", actor_id=actor)
    assert previous is True and saved["enabled"] is False
    async with runtime.pool.connection() as connection:
        rows = await (await connection.execute(
            """select actor_user_id,old_enabled,new_enabled from owner_reporting.automation_changes
               where target_user_id=%s order by id""",
            (target,),
        )).fetchall()
    assert rows == [(actor, False, True), (actor, True, False)]
    with pytest.raises(AppError):
        await runtime.data.set_automation_enabled("global", False, model_id="m")
