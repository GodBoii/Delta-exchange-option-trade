"""Regression coverage for recovery admission, account isolation and local profiles."""

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from psycopg_pool import AsyncConnectionPool

from app import engine as engine_module
from app.engine import Scheduler, TradingEngine
from app.keyed_locks import KeyedLocks
from app.profiles import Profiles
from app.recovery import Recovery
from app.strategy_cursor import decode_strategy_cursor, encode_strategy_cursor

database = pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="Disposable PostgreSQL required")


async def test_unrelated_session_can_open_while_another_credentials_lookup_waits(monkeypatch):
    blocked, release = asyncio.Event(), asyncio.Event()

    async def credentials(_db, user_id):
        if user_id == "slow":
            blocked.set()
            await release.wait()
        return {"api_key": user_id, "api_secret": "test", "delta_user_id": user_id}

    monkeypatch.setattr(engine_module, "credentials_for_user", credentials)
    settings = SimpleNamespace(delta_events_enabled=False, delta_production_url="https://test.invalid")
    engine = TradingEngine(SimpleNamespace(runtime=SimpleNamespace(pool=None)), settings)
    slow = asyncio.create_task(engine.client_for_user("slow"))
    try:
        await blocked.wait()
        fast = await asyncio.wait_for(engine.client_for_user("fast"), 1)
        assert not slow.done()
        await fast.close()
    finally:
        release.set()
        await (await slow).close()
        await engine.close()
    assert not engine.session_locks.entries


async def test_keyed_lock_releases_cancelled_waiters_and_keeps_same_key_serial():
    locks = KeyedLocks()
    async with locks.hold("wallet"):
        async def wait():
            async with locks.hold("wallet"):
                raise AssertionError("Must not enter")
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert locks.entries["wallet"].users == 1
    assert not locks.entries


async def test_never_dispatched_recovery_does_not_query_other_or_expired_positions():
    engine = TradingEngine.__new__(TradingEngine)
    engine.db = SimpleNamespace(update=AsyncMock(return_value=[{"id": "run"}]))
    engine.run_orders = AsyncMock(return_value=[])
    engine.release_capital_slot = AsyncMock()
    journal = SimpleNamespace(account_id="india:1", strategy_intents=AsyncMock(return_value=[]), call=AsyncMock())
    client = SimpleNamespace(order_journal=journal, position=AsyncMock(), positions=AsyncMock())
    row = {"id": "run", "user_id": "owner", "risk_state": {
        "exclusiveFillAccounting": True, "exchangeAccount": "india:1", "plannedProductIds": [99],
    }}
    assert await engine.resolve_unsubmitted_run(row, client)
    client.position.assert_not_awaited()
    client.positions.assert_not_awaited()
    engine.release_capital_slot.assert_awaited_once()
    journal.strategy_intents.return_value = [{"outcome": {"kind": "unknown"}}]
    assert not await engine.resolve_unsubmitted_run(row, client)
    journal.strategy_intents.return_value = [{"outcome": {"kind": "accepted"}}]
    assert not await engine.resolve_unsubmitted_run(row, client)
    journal.strategy_intents.return_value = []
    engine.run_orders.return_value = [{"delta_order_id": "accepted", "filled_size": "1"}]
    assert not await engine.resolve_unsubmitted_run(row, client)


async def test_risk_scheduler_runs_during_slow_execution_batch():
    processing, release, risk_checked = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def due(**kwargs):
        assert kwargs == {"include_risks": False}
        processing.set()
        await release.wait()

    async def risk():
        await processing.wait()
        risk_checked.set()

    engine = SimpleNamespace(startup_recovered=True, wake=asyncio.Event(), risk_errors={},
                             recover_interrupted_states=AsyncMock(), process_due_strategies=due,
                             cleanup_sessions=AsyncMock(), observe_recovery_positions=AsyncMock(),
                             process_active_risks=risk)
    scheduler = Scheduler(engine, 0.01)
    scheduler.start()
    try:
        await asyncio.wait_for(risk_checked.wait(), 1)
        assert scheduler.last_completed_at is None
    finally:
        release.set()
        await scheduler.stop()


async def test_escalated_exposure_remains_observable_without_order_or_position_requests():
    from unittest.mock import Mock

    engine = TradingEngine.__new__(TradingEngine)
    row = {"id": "run", "user_id": "owner", "updated_at": datetime.now(UTC).isoformat(),
           "risk_state": {"exposureStatus": "open", "recoveryStatus": "manual_review", "exchangeAccount": "india:1"}}
    engine.strategy_pages = AsyncMock(return_value=[row])
    engine.entry_orders = AsyncMock(return_value=[{"product_symbol": "contract", "filled_size": "1"}])
    events = SimpleNamespace(closed=False, watch_mark=Mock(return_value="42"))
    engine.sessions = {"owner": SimpleNamespace(credentials={"delta_user_id": "1"}, events=events, last_used=0)}
    engine.db = SimpleNamespace(update=AsyncMock())
    engine.client_for_user = AsyncMock()
    await engine.observe_recovery_positions()
    value = engine.db.update.await_args.args[1]["risk_state"]
    assert value["exposureStatus"] == "open" and value["recoveryStatus"] == "manual_review"
    assert value["recoveryObservation"]["marks"] == {"contract": "42"}
    engine.client_for_user.assert_not_awaited()


def test_history_cursor_round_trip_and_invalid_input():
    from app.errors import AppError

    row = {"id": str(uuid4()), "created_at": datetime.now(UTC).isoformat()}
    instant, identifier = decode_strategy_cursor(encode_strategy_cursor(row))
    assert instant.isoformat() == row["created_at"] and identifier == row["id"]
    for value in ("invalid", "W10=", "e30=", "bnVsbA=="):
        with pytest.raises(AppError):
            decode_strategy_cursor(value)


@database
async def test_profile_bootstrap_preserves_owner_and_local_changes_survive_remote_failure():
    owner = str(uuid4())
    requests = []
    unavailable = False

    def remote(request):
        requests.append(request)
        if unavailable:
            return httpx.Response(503)
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": owner, "user_type": "owner", "display_name": "Original",
                                            "email": "owner@test.invalid", "created_at": "2026-01-01T00:00:00Z"}])
        return httpx.Response(204)

    settings = SimpleNamespace(supabase_url="https://test.invalid", supabase_service_role_key="test")
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as client:
            profiles = Profiles(pool, client, settings)
            assert (await profiles.ensure(owner))["user_type"] == "owner"
            await profiles.save(owner, {"display_name": "Local edit"})
            unavailable = True
            assert (await profiles.ensure(owner))["display_name"] == "Local edit"
            await profiles.mirror()
            async with pool.connection() as connection:
                result = await connection.execute(
                    "select revision,mirrored_revision,sync_attempts from trade.profiles where id=%s", (owner,)
                )
                revision, mirrored, attempts = await result.fetchone()
                assert revision > mirrored and attempts == 1
                await connection.execute("update trade.profiles set next_sync_at=now() where id=%s", (owner,))
            # A restarted worker continues from the same durable pending revision.
            unavailable = False
            await Profiles(pool, client, settings).mirror()
            await profiles.import_rows([{"id": owner, "user_type": "user", "display_name": "Older mirror"}])
            assert (await profiles.ensure(owner))["user_type"] == "owner"
            assert (await profiles.ensure(owner))["display_name"] == "Local edit"
            assert sum(r.method == "GET" for r in requests) == 1
            async with pool.connection() as connection:
                await connection.execute("delete from trade.profiles where id=%s", (owner,))


@database
async def test_profile_direct_sql_edits_queue_mirror_and_concurrent_edits_are_not_lost():
    import json

    identifier = str(uuid4())
    sent = []
    settings = SimpleNamespace(supabase_url="https://test.invalid", supabase_service_role_key="test")
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()

        async def remote(request):
            sent.append(json.loads(request.content))
            if len(sent) == 1:
                await profiles.save(identifier, {"display_name": "Newer edit"})
            return httpx.Response(204)

        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as client:
            profiles = Profiles(pool, client, settings)
            await profiles.import_rows([{"id": identifier, "user_type": "owner", "display_name": "Initial"}])
            async with pool.connection() as connection:
                await connection.execute(
                    """update trade.profiles set data=jsonb_set(data,'{display_name}','"SQL edit"')
                       || '{"local_only_note":"never mirrored"}'::jsonb where id=%s""",
                    (identifier,),
                )
            await profiles.mirror()
            assert sent[0]["display_name"] == "SQL edit"
            assert "local_only_note" not in sent[0]
            assert (await profiles.status())["pending"] >= 1
            await profiles.mirror()
            assert sent[-1]["display_name"] == "Newer edit"
            assert (await profiles.get(identifier))["user_type"] == "owner"
            async with pool.connection() as connection:
                result = await connection.execute(
                    "select revision=mirrored_revision from trade.profiles where id=%s", (identifier,)
                )
                assert (await result.fetchone())[0]
                await connection.execute("delete from trade.profiles where id=%s", (identifier,))


@database
async def test_recovery_attempts_are_capped_across_restarts_and_resumable():
    strategy = str(uuid4())
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        for attempt in range(5):
            retry = Recovery(pool)
            assert await retry.claim(strategy, "reconcile")
            assert await retry.failed(strategy, "reconcile", "delta_unreachable") == (attempt == 4)
            assert not await retry.claim(strategy, "reconcile")
            async with pool.connection() as connection:
                await connection.execute(
                    "update trade.recovery_attempts set next_attempt_at=now() where strategy_id=%s", (strategy,)
                )
        assert not await Recovery(pool).claim(strategy, "reconcile")
        await retry.clear(strategy)
        assert await Recovery(pool).claim(strategy, "reconcile")


@database
async def test_sql_projection_preserves_missing_null_and_does_not_fetch_raw_response():
    from app.local_runtime import LocalRuntimeStore

    owner, strategy = str(uuid4()), str(uuid4())
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        await runtime.write("strategies", {"id": strategy, "user_id": owner, "status": "draft",
                                           "response_json": {"large": "x" * 100000}})
        assert await runtime.select("strategies", {"id": f"eq.{strategy}", "select": "id,absent"}) == [
            {"id": strategy, "absent": None}
        ]


async def test_overview_metadata_refresh_makes_no_exchange_requests(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "current_account", AsyncMock(return_value={"delta_user_id": "1", "account_name": "Main"}))
    engine = SimpleNamespace(client_for_user=AsyncMock())
    db = SimpleNamespace(select=AsyncMock(return_value=[]))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, engine=engine)))
    result = await main.account_overview(request, {"id": "owner"}, includeExchange=False)
    assert result["riskStrategies"] == [] and "positions" not in result
    engine.client_for_user.assert_not_awaited()


async def test_subaccount_cleanup_does_not_retry_an_exhausted_release():
    from contextlib import asynccontextmanager

    from app.account_manager import AccountManager

    cursor = SimpleNamespace(execute=AsyncMock(), fetchall=AsyncMock(return_value=[{
        "owner_id": "owner", "strategy_id": "run",
    }]))

    @asynccontextmanager
    async def context():
        yield cursor

    connection = SimpleNamespace(cursor=lambda **kwargs: context())

    @asynccontextmanager
    async def connect():
        yield connection

    manager = AccountManager.__new__(AccountManager)
    manager.registry = SimpleNamespace(pool=SimpleNamespace(connection=connect))
    manager.engine = SimpleNamespace(recovery=SimpleNamespace(claim=AsyncMock(return_value=False)))
    manager.release = AsyncMock()
    await manager.release_finished()
    manager.release.assert_not_awaited()


async def test_order_listing_reads_every_page_and_rejects_repeated_cursor():
    from app.delta import DeltaClient
    from app.errors import AppError

    client = DeltaClient.__new__(DeltaClient)
    client.open_orders = AsyncMock(side_effect=[
        {"result": [{"id": 1}], "meta": {"after": "next"}},
        {"result": [{"id": 2}], "meta": {}},
    ])
    assert (await client.all_open_orders())["result"] == [{"id": 1}, {"id": 2}]
    client.open_orders.side_effect = [
        {"result": [], "meta": {"after": "next"}}, {"result": [], "meta": {"after": "next"}},
    ]
    with pytest.raises(AppError, match="repeated"):
        await client.all_open_orders()


@database
async def test_strategy_keyset_pagination_handles_equal_timestamps_and_large_history():
    from app.local_runtime import LocalRuntimeStore

    owner, created = str(uuid4()), datetime.now(UTC).isoformat()
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        identifiers = [str(uuid4()) for _ in range(105)]
        for identifier in identifiers:
            await runtime.write("strategies", {
                "id": identifier, "user_id": owner, "status": "draft", "created_at": created,
            })
        query = {"user_id": f"eq.{owner}", "order": "created_at.desc,id.desc", "limit": "100"}
        first = await runtime.select("strategies", query)
        second = await runtime.select("strategies", {**query, "cursor": encode_strategy_cursor(first[-1])})
        assert len(first) == 100 and len(second) == 5
        assert {row["id"] for row in first + second} == set(identifiers)
