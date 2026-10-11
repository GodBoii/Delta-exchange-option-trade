"""Admission behavior with mixed accounts, concurrent work and durable commitments."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app import automation
from app.analysis_admission import evaluate
from app.engine import TradingEngine
from app.errors import AppError
from app.exit_schedule import session_expiry
from app.local_runtime import LocalRuntimeStore


def policy(mode, amount):
    return {"allocation_mode": mode, "capital_amount": str(amount) if amount is not None else None}


def snapshot(mode="full_balance", amount=None, occupied=0, reserved="0", commitments=None):
    return {
        "eligible": True, "owner": "account", "capital": policy(mode, amount),
        "occupied": occupied, "reserved": reserved, "commitments": commitments or [],
    }


def commitment(mode="full_balance", amount=None, owner="account", start=0, end=60):
    base = datetime(2026, 10, 12, tzinfo=UTC)
    return {
        "owner": owner, "capital": policy(mode, amount),
        "start": (base + timedelta(minutes=start)).isoformat(),
        "end": (base + timedelta(minutes=end)).isoformat(),
    }


@pytest.mark.parametrize("mode,count", [
    ("full_balance", 1), ("half_balance", 2), ("one_third_balance", 3), ("one_quarter_balance", 4),
])
def test_percentage_commitments_use_remaining_account_slots(mode, count):
    pending = [commitment(mode) for _ in range(count - 1)]
    assert evaluate(snapshot(mode, commitments=pending), Decimal(120), Decimal(120)) == "ready"
    pending.append(commitment(mode))
    assert evaluate(snapshot(mode, commitments=pending), Decimal(120), Decimal(120)) == "pending"
    assert evaluate(snapshot(mode, occupied=count), Decimal(120), Decimal(120)) == "full"


def test_large_fixed_account_supports_parallel_assets_and_hundred_slots():
    pending = [commitment("fixed_amount", Decimal(100))]
    state = snapshot("fixed_amount", Decimal(100), occupied=48, reserved="4800", commitments=pending)
    assert evaluate(state, Decimal(5200), Decimal(10000)) == "ready"
    state["occupied"], state["reserved"] = 99, "9900"
    assert evaluate(state, Decimal(100), Decimal(10000)) == "pending"


def test_alias_policies_share_money_but_keep_existing_per_login_slot_limits():
    alias = commitment("half_balance", owner="alias")
    assert evaluate(snapshot("half_balance", commitments=[alias]), Decimal(100), Decimal(100)) == "ready"
    assert evaluate(snapshot(commitments=[alias]), Decimal(100), Decimal(100)) == "pending"


def test_disjoint_commitments_are_not_added_together():
    pending = [commitment("half_balance", start=0, end=30), commitment("half_balance", start=30, end=60)]
    assert evaluate(snapshot("half_balance", commitments=pending), Decimal(100), Decimal(100)) == "ready"
    pending[1]["start"] = commitment(start=29)["start"]
    assert evaluate(snapshot("half_balance", commitments=pending), Decimal(100), Decimal(100)) == "pending"


@pytest.mark.parametrize("available,total", [(0, 100), (100, 0), (-1, 100)])
def test_no_positive_wallet_capacity_is_full(available, total):
    assert evaluate(snapshot(), Decimal(available), Decimal(total)) == "full"


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_invalid_wallet_is_unknown(value):
    with pytest.raises(AppError, match="invalid"):
        evaluate(snapshot(), Decimal(value), Decimal(100))


async def test_shared_asset_filter_and_healthy_large_account_override_pending():
    db = SimpleNamespace(select=AsyncMock(return_value=[
        {"user_id": "btc_only", "asset_enabled": {"ETH": False}},
        {"user_id": "small"}, {"user_id": "large"},
    ]))
    engine = SimpleNamespace(analysis_trade_capacity=AsyncMock(side_effect=["pending", "ready"]))
    assert await automation.has_automation_trade_capacity(db, engine, "global", run={"asset": "ETH"})
    checked = {call.args[0] for call in engine.analysis_trade_capacity.await_args_list}
    assert checked == {"small", "large"}


async def test_shared_unknown_never_becomes_full_and_pending_is_distinct():
    db = SimpleNamespace(select=AsyncMock(return_value=[{"user_id": "account"}]))
    engine = SimpleNamespace(analysis_trade_capacity=AsyncMock(return_value="pending"))
    with pytest.raises(AppError) as caught:
        await automation.has_automation_trade_capacity(db, engine, "global", run={"asset": "ETH"})
    assert caught.value.code == "analysis_capacity_pending"
    engine.analysis_trade_capacity.side_effect = TimeoutError("wallet")
    with pytest.raises(AppError) as caught:
        await automation.has_automation_trade_capacity(db, engine, "global", run={"asset": "ETH"})
    assert caught.value.code == "capital_capacity_unavailable"


@pytest.mark.parametrize("unavailable", [False, True])
async def test_post_claim_competition_returns_to_queue_without_analyzer(monkeypatch, unavailable):
    db = SimpleNamespace(update=AsyncMock())
    engine = SimpleNamespace(analysis_trade_capacity=AsyncMock(return_value="pending"))
    if unavailable:
        engine.analysis_trade_capacity.side_effect = TimeoutError("wallet")
    monkeypatch.setattr(automation.httpx, "AsyncClient", lambda **_: pytest.fail("No model request"))
    result = await automation.execute_automation_run(
        db=db, engine=engine, user_id="account", run_id="run", session_id="run",
        trigger="manual", reason="manual", asset="ETH",
    )
    assert result["deferred"] and result["status"] == "scheduled"
    values, filters = db.update.await_args.args[1:]
    assert values["started_at"] is None and filters["outcome"] == "is.null"


async def test_scheduler_starts_btc_first_and_reconsiders_eth_after_no_trade(monkeypatch):
    now = datetime.now(UTC).isoformat()
    rows = [{"id": asset, "user_id": "account", "asset": asset, "trigger": "manual",
             "scheduled_for": now, "status": "scheduled"} for asset in ("ETH", "BTC")]
    checked, started = [], []

    class Database:
        settings = SimpleNamespace(shared_analysis_enabled=False)

        async def select(self, table, params):
            if table == "automation_settings":
                return [{"user_id": "account"}]
            return [row for row in rows if row["status"] == "scheduled"] if (
                params["trigger"] == "neq.activation_recheck"
            ) else []

        async def update(self, _table, _values, _params):
            return []

        async def rpc(self, _name, args):
            row = next(row for row in rows if row["id"] == args["p_run_id"])
            row["status"] = "running"
            return [row.copy()]

    async def capacity(_owner, run):
        checked.append(run["asset"])
        return "pending" if run["asset"] == "ETH" and rows[1]["status"] == "running" else "ready"

    scheduler = automation.AutomationScheduler(Database(), SimpleNamespace(analysis_trade_capacity=capacity))
    monkeypatch.setattr(automation, "require_analyzer_ready", AsyncMock())

    async def execute(row):
        started.append(row["asset"])

    scheduler._execute = execute
    await scheduler._process_due_runs()
    await asyncio.gather(*scheduler.running_tasks)
    assert checked == ["BTC", "ETH"] and started == ["BTC"]
    assert rows[0]["status"] == "scheduled"
    rows[1]["status"] = "completed"
    await scheduler._process_due_runs()
    await asyncio.gather(*scheduler.running_tasks)
    assert started == ["BTC", "ETH"]


@pytest.fixture(scope="module")
def admission_database():
    source = os.getenv("TEST_LOCAL_DATABASE_URL")
    if not source:
        pytest.skip("No isolated PostgreSQL URL")
    name = f"admission_{uuid4().hex}"
    with psycopg.connect(source, autocommit=True) as connection:
        try:
            connection.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("Admission integration tests require CREATEDB on the disposable server")
    params = conninfo_to_dict(source)
    params["dbname"] = name
    target = make_conninfo(**params)
    try:
        from scripts.init_local_db import apply_migrations
        apply_migrations(target)
        yield target
    finally:
        with psycopg.connect(source, autocommit=True) as connection:
            connection.execute(sql.SQL("drop database {}").format(sql.Identifier(name)))


@pytest.fixture
async def runtime(admission_database):
    async with AsyncConnectionPool(admission_database, open=False) as pool:
        await pool.open()
        started = datetime.now(UTC)
        try:
            yield LocalRuntimeStore(pool)
        finally:
            async with pool.connection() as connection:
                for table in ("analysis_jobs", "strategy_proposals"):
                    # The fixture runs only on the explicitly configured disposable database.
                    await connection.execute(
                        f"delete from trade.{table} where owner_id='global' and created_at >= %s", (started,),
                    )


async def account(runtime, mode="full_balance", amount=None, wallet=None, asset_enabled=None):
    owner = str(uuid4())
    async with runtime.pool.connection() as connection:
        await connection.execute(
            "insert into trade.users (user_id,connection,automation,capital,record) values (%s,%s,%s,%s,%s)",
            (owner, Jsonb({"status": "connected", "delta_user_id": wallet or owner}),
             Jsonb({"enabled": True, "asset_enabled": asset_enabled or {}}),
             Jsonb(policy(mode, amount)), Jsonb({})),
        )
    return owner


async def job(runtime, asset="BTC", status="running", owner="global", outcome=None):
    now = datetime.now(UTC).isoformat()
    row = {"id": str(uuid4()), "user_id": owner, "asset": asset, "status": status,
           "trigger": "manual", "scheduled_for": now, "started_at": now, "outcome": outcome}
    await runtime.write("automation_agent_runs", row)
    return row


async def read(runtime, owner, run):
    return await runtime.rpc("get_analysis_capacity_snapshot", {
        "p_user_id": owner, "p_run": run, "p_entry_lateness_seconds": 180,
    })


async def decision(runtime, asset="BTC"):
    now = datetime.now(UTC)
    remaining = session_expiry(now) - now
    proposal = {"id": str(uuid4()), "user_id": "global", "asset": asset, "status": "scheduled",
                "activation_time": (now + remaining / 4).isoformat(),
                "exit_at": (now + remaining / 2).isoformat(), "shared_recheck_run_id": str(uuid4())}
    await runtime.write("strategy_proposals", proposal)
    await runtime.write("automation_agent_runs", {
        "id": proposal["shared_recheck_run_id"], "user_id": "global", "asset": asset,
        "status": "scheduled", "trigger": "activation_recheck", "strategy_proposal_id": proposal["id"],
        "scheduled_for": now.isoformat(),
    })
    return proposal


async def test_postgres_running_job_dedup_and_order_survive_concurrent_claims(runtime):
    owner = await account(runtime)
    first = await job(runtime)
    second = await job(runtime, asset="ETH")
    assert evaluate(await read(runtime, owner, first), Decimal(100), Decimal(100)) == "ready"
    assert evaluate(await read(runtime, owner, second), Decimal(100), Decimal(100)) == "pending"
    await runtime.update("automation_agent_runs", {"status": "completed", "outcome": "no_trade_for_current_window"},
                         {"id": f"eq.{first['id']}"})
    assert evaluate(await read(runtime, owner, second), Decimal(100), Decimal(100)) == "ready"


async def test_postgres_recheck_owns_commitment_and_btc_wins_legacy_competition(runtime):
    owner = await account(runtime)
    btc, eth = await decision(runtime), await decision(runtime, "ETH")
    assert evaluate(await read(runtime, owner, {"asset": "ETH"}), Decimal(100), Decimal(100)) == "pending"
    assert evaluate(await read(runtime, owner, {"asset": "BTC", "strategy_proposal_id": btc["id"]}),
                    Decimal(100), Decimal(100)) == "ready"
    assert evaluate(await read(runtime, owner, {"asset": "ETH", "strategy_proposal_id": eth["id"]}),
                    Decimal(100), Decimal(100)) == "pending"
    await runtime.update("automation_agent_runs", {"status": "cancelled"}, {"id": f"eq.{btc['shared_recheck_run_id']}"})
    assert evaluate(await read(runtime, owner, {"asset": "ETH", "strategy_proposal_id": eth["id"]}),
                    Decimal(100), Decimal(100)) == "ready"


async def test_postgres_shared_and_account_copies_count_once_until_real_reservation(runtime):
    owner = await account(runtime, "half_balance")
    proposed = await decision(runtime)
    strategy = {"id": str(uuid4()), "user_id": owner, "asset": "BTC", "status": "scheduled",
                "shared_decision_id": proposed["id"], "entry_at": proposed["activation_time"],
                "exit_at": proposed["exit_at"]}
    await runtime.write("strategies", strategy)
    await runtime.write("strategy_proposals", {
        "id": str(uuid4()), "user_id": owner, "asset": "BTC", "status": "scheduled",
        "strategy_id": strategy["id"], "shared_decision_id": proposed["id"],
        "activation_time": proposed["activation_time"], "shared_recheck_run_id": proposed["shared_recheck_run_id"],
    })
    state = await read(runtime, owner, {"asset": "ETH"})
    assert len(state["commitments"]) == 1
    assert evaluate(state, Decimal(100), Decimal(100)) == "ready"
    await runtime.rpc("reserve_strategy_capital_slot", {
        "p_user_id": owner, "p_strategy_id": strategy["id"], "p_maximum_slots": 2,
        "p_budget": "50", "p_total_balance": "100",
    })
    state = await read(runtime, owner, {"asset": "ETH"})
    assert state["occupied"] == 1 and not state["commitments"]
    assert evaluate(state, Decimal(50), Decimal(100)) == "ready"


async def test_postgres_alias_budget_and_asset_participation(runtime):
    wallet = str(uuid4())
    owner = await account(runtime, "half_balance", wallet=wallet, asset_enabled={"BTC": False})
    await account(runtime, "full_balance", wallet=wallet)
    await job(runtime)
    assert evaluate(await read(runtime, owner, {"asset": "ETH"}), Decimal(100), Decimal(100)) == "pending"
    assert (await read(runtime, owner, {"asset": "BTC"}))["eligible"] is False


@pytest.mark.parametrize("outcome", ["wait_and_run_again", "no_trade_for_current_window"])
async def test_postgres_non_selection_releases_analysis_commitment(runtime, outcome):
    owner = await account(runtime)
    btc = await job(runtime, outcome=outcome)
    assert evaluate(await read(runtime, owner, {"asset": "ETH"}), Decimal(100), Decimal(100)) == "ready"
    await runtime.update("automation_agent_runs", {"outcome": None, "status": "scheduled"},
                         {"id": f"eq.{btc['id']}"})
    assert evaluate(await read(runtime, owner, {"asset": "ETH"}), Decimal(100), Decimal(100)) == "pending"


async def test_postgres_private_recheck_does_not_count_itself_or_drop_unrelated_manual_work(runtime):
    owner = await account(runtime)
    now = datetime.now(UTC)
    remaining = session_expiry(now) - now
    strategy_id, proposal_id, recheck_id = (str(uuid4()) for _ in range(3))
    strategy = {"id": strategy_id, "user_id": owner, "status": "scheduled", "asset": "BTC",
                "entry_at": (now + remaining / 4).isoformat(), "exit_at": (now + remaining / 2).isoformat()}
    await runtime.write("strategies", strategy)
    await runtime.write("strategy_proposals", {
        "id": proposal_id, "user_id": owner, "asset": "BTC", "status": "scheduled",
        "strategy_id": strategy_id, "activation_time": strategy["entry_at"],
    })
    await runtime.write("automation_agent_runs", {
        "id": recheck_id, "user_id": owner, "asset": "BTC", "trigger": "activation_recheck",
        "strategy_proposal_id": proposal_id, "status": "running", "scheduled_for": now.isoformat(),
        "started_at": now.isoformat(),
    })
    assert evaluate(await read(runtime, owner, {"asset": "BTC", "strategy_proposal_id": proposal_id}),
                    Decimal(100), Decimal(100)) == "ready"
    await runtime.update("automation_agent_runs", {"status": "failed"}, {"id": f"eq.{recheck_id}"})
    await runtime.write("strategies", {**strategy, "id": str(uuid4()), "asset": "ETH"})
    state = await read(runtime, owner, {"asset": "ETH"})
    assert len(state["commitments"]) == 1
    assert evaluate(state, Decimal(100), Decimal(100)) == "pending"


async def test_postgres_nonoverlapping_recheck_uses_its_assigned_window(runtime):
    owner = await account(runtime)
    btc, eth = await decision(runtime), await decision(runtime, "ETH")
    now = datetime.now(UTC)
    remaining = session_expiry(now) - now
    await runtime.update("strategy_proposals", {
        "activation_time": btc["exit_at"], "exit_at": (now + remaining * 3 / 4).isoformat(),
    }, {"id": f"eq.{eth['id']}"})
    assert evaluate(await read(runtime, owner, {"asset": "ETH", "strategy_proposal_id": eth["id"]}),
                    Decimal(100), Decimal(100)) == "ready"


async def test_postgres_two_full_balance_accounts_run_only_btc_when_it_consumes_both(runtime, monkeypatch):
    owners = [await account(runtime), await account(runtime)]
    now = datetime.now(UTC).isoformat()
    jobs = [await job(runtime, asset=asset, status="scheduled") for asset in ("ETH", "BTC")]

    class Database:
        settings = SimpleNamespace(shared_analysis_enabled=True)

        async def select(self, table, params):
            if table == "automation_settings":
                return ([{"user_id": "global", "enabled": True}] if params.get("user_id") == "eq.global"
                        else [{"user_id": owner} for owner in owners])
            return await runtime.select(table, params)

        async def update(self, table, values, params):
            return await runtime.update(table, values, params)

        async def rpc(self, name, args):
            return await runtime.rpc(name, args)

    engine = TradingEngine(Database(), SimpleNamespace())
    client = SimpleNamespace(close=AsyncMock())
    engine.client_for_user = AsyncMock(return_value=client)
    engine.usd_capital = AsyncMock(return_value=(Decimal(100), Decimal(100)))
    scheduler = automation.AutomationScheduler(engine.db, engine)
    scheduler._execute = AsyncMock()
    monkeypatch.setattr(automation, "require_analyzer_ready", AsyncMock())
    try:
        await scheduler._process_due_runs()
        await asyncio.gather(*scheduler.running_tasks)
        assert [call.args[0]["asset"] for call in scheduler._execute.await_args_list] == ["BTC"]
        proposed = await decision(runtime)
        await runtime.update("automation_agent_runs", {"status": "completed", "outcome": "strategy_selected"},
                             {"id": f"eq.{jobs[1]['id']}"})
        await scheduler._process_due_runs()
        await asyncio.gather(*scheduler.running_tasks)
        assert [call.args[0]["asset"] for call in scheduler._execute.await_args_list
                if call.args[0]["trigger"] != "activation_recheck"] == ["BTC"]
        for owner in owners:
            strategy_id = str(uuid4())
            await runtime.write("strategies", {
                "id": strategy_id, "user_id": owner, "status": "active", "asset": "BTC",
                "shared_decision_id": proposed["id"], "entry_at": now, "exit_at": proposed["exit_at"],
            })
            await runtime.rpc("reserve_strategy_capital_slot", {
                "p_user_id": owner, "p_strategy_id": strategy_id, "p_maximum_slots": 1,
                "p_budget": "100", "p_total_balance": "100",
            })
        await scheduler._process_due_runs()
        eth = (await runtime.select("automation_agent_runs", {"id": f"eq.{jobs[0]['id']}"}))[0]
        assert eth["status"] == "cancelled" and eth["error"] == automation.NO_TRADE_SLOTS_MESSAGE
        assert all(call.args[0]["asset"] == "BTC" for call in scheduler._execute.await_args_list)
    finally:
        await engine.close()


async def test_engine_full_slot_avoids_wallet_and_pending_closes_client():
    state = snapshot(occupied=1)
    db = SimpleNamespace(settings=SimpleNamespace(), rpc=AsyncMock(return_value=state))
    engine = TradingEngine(db, SimpleNamespace())
    client = SimpleNamespace(close=AsyncMock())
    engine.client_for_user = AsyncMock(return_value=client)
    engine.usd_capital = AsyncMock(return_value=(Decimal(100), Decimal(100)))
    try:
        assert await engine.analysis_trade_capacity("account", {"asset": "BTC"}) == "full"
        engine.client_for_user.assert_not_awaited()
        state["occupied"], state["commitments"] = 0, [commitment()]
        assert await engine.analysis_trade_capacity("account", {"asset": "ETH"}) == "pending"
        client.close.assert_awaited_once()
    finally:
        await engine.close()
