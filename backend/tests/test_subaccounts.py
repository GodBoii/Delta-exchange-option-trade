"""Subaccount isolation and funding safety against disposable PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from psycopg.errors import RaiseException
from psycopg_pool import AsyncConnectionPool

from app.account_funding import transfer_time, usd_wallet
from app.auth import guard_subaccount_connections
from app.capital import CapitalPolicy
from app.config import Settings
from app.credential_store import account_store
from app.database import Database
from app.delta import DeltaClient
from app.engine import TradingEngine
from app.errors import AppError


@pytest.mark.parametrize("value", [None, {}, {"result": []}, {"result": [{"asset_symbol": "USD", "balance": "NaN"}]}])
def test_invalid_wallet_is_never_zero_capital(value):
    with pytest.raises((AppError, AttributeError)):
        usd_wallet(value)


def test_transfer_timestamps_support_delta_formats():
    expected = datetime(2026, 10, 17, tzinfo=UTC)
    assert transfer_time(str(int(expected.timestamp() * 1_000_000))) == expected
    assert transfer_time(expected.isoformat()) == expected
    assert transfer_time("unknown") is None


@pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="Isolated PostgreSQL required")
async def test_same_contract_can_be_owned_independently_across_children(desk):
    runs = [await desk.run(), await desk.run()]
    for run, account in zip(runs, [desk.child, desk.second], strict=True):
        await desk.registry.reserve(desk.owner, run["id"], account)
        client = await desk.engine.client_for_run(run)
        try:
            await client.order_journal.call(
                "orderIntents:claimProducts", {"strategyId": run["id"], "productIds": ["123"]}
            )
        finally:
            await client.close()
    async with desk.db.pool.connection() as connection:
        cursor = await connection.execute(
            "select count(*) from trade.product_claims where account_id=any(%s)",
            ([f"india:{desk.child}", f"india:{desk.second}"],),
        )
        assert (await cursor.fetchone())[0] == 2


@pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="Isolated PostgreSQL required")
async def test_child_exit_credentials_survive_main_connection_revocation(desk):
    run = await desk.run()
    await desk.registry.reserve(desk.owner, run["id"], desk.child)
    await desk.db.local_data.request("accounts:revoke", {"userId": desk.owner}, mutation=True)
    client = await desk.engine.client_for_run(run)
    assert client.api_key == "child-key"
    await client.close()


@pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="Isolated PostgreSQL required")
async def test_auto_funding_returns_flat_surplus_before_entry(desk):
    desk.balances.update({"main-key": Decimal(500), "child-key": Decimal(500)})
    await desk.registry.save_settings(desk.owner, "automatic", True, 0)
    assigned = await desk.registry.reserve(desk.owner, (await desk.run())["id"], desk.child)
    await desk.engine.subaccounts.fund(desk.owner, assigned, Decimal(250), Decimal(1000))
    assert desk.balances["child-key"] == 250 and desk.balances["main-key"] == 750


database_test = pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="Isolated PostgreSQL required")


@pytest.fixture
async def desk(monkeypatch):
    url = os.environ["TEST_LOCAL_DATABASE_URL"]
    settings = Settings(
        _env_file=None, LOCAL_DATABASE_URL=url, CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode()
    )
    async with AsyncConnectionPool(url, min_size=1, max_size=8, open=False) as pool:
        await pool.open()
        db = Database(settings, pool)
        owner = str(uuid4())
        parent = str(uuid4().int % 10**12)
        child, second = str(int(parent) + 1), str(int(parent) + 2)
        await db.local_data.ensure_user(owner)
        await account_store(db).save(owner, "main-key", "main-secret", {"id": parent, "account_name": "Main"})
        engine = TradingEngine(db, settings)
        registry = engine.subaccounts.registry
        calls = []
        balances = {"main-key": Decimal(1000), "child-key": Decimal(0), "second-key": Decimal(0)}
        profiles = {"main-key": parent, "child-key": child, "second-key": second}
        behavior = {"transfer": "confirmed", "positions": [], "orders": [], "history": []}

        async def exchange(client, method, path, *, query=None, body=None, authenticated=False):
            calls.append((client.api_key, method, path, body))
            if path == "/v2/profile":
                return {"result": {"id": profiles[client.api_key]}}
            if path == "/v2/wallet/balances":
                amount = balances[client.api_key]
                return {"result": [{"asset_symbol": "USD", "balance": str(amount), "available_balance": str(amount)}]}
            if path == "/v2/positions/margined":
                return {"result": behavior["positions"]}
            if path == "/v2/orders":
                return {"result": behavior["orders"]}
            if path == "/v2/sub_accounts":
                return {
                    "result": [
                        {"id": parent, "account_name": "Main", "is_sub_account": False},
                        {"id": child, "account_name": "Child", "is_sub_account": True},
                        {"id": second, "account_name": "Second", "is_sub_account": True},
                    ]
                }
            if path == "/v2/wallets/sub_accounts_transfer_history":
                assert body is None and query["page_size"] == 100
                return {"result": behavior["history"]}
            if path == "/v2/wallets/sub_account_balance_transfer":
                if behavior["transfer"] == "timeout":
                    raise AppError(502, "Network timeout", "delta_unreachable")
                if behavior["transfer"] == "rejected":
                    raise AppError(400, "Rejected", "insufficient_balance")
                amount = Decimal(body["amount"])
                source = next(key for key, identifier in profiles.items() if identifier == body["transferrer_user_id"])
                target = next(key for key, identifier in profiles.items() if identifier == body["transferee_user_id"])
                balances[source] -= amount
                balances[target] += amount
                return {"success": True, "result": None}
            raise AssertionError(f"Unexpected exchange call: {path}")

        monkeypatch.setattr(DeltaClient, "request", exchange)
        await registry.sync(owner, await exchange(SimpleNamespace(api_key="main-key"), "GET", "/v2/sub_accounts"))
        for account, key in ((child, "child-key"), (second, "second-key")):
            await registry.save_key(owner, account, key, "secret")
            await registry.set_participation(owner, account, "automatic")

        async def run():
            identifier = str(uuid4())
            row = {
                "id": identifier,
                "user_id": owner,
                "name": "Run",
                "status": "scheduled",
                "entry_at": datetime.now(UTC).isoformat(),
                "exit_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
            await db.runtime.write("strategies", row)
            return row

        yield SimpleNamespace(
            db=db,
            engine=engine,
            registry=registry,
            owner=owner,
            parent=parent,
            child=child,
            second=second,
            calls=calls,
            balances=balances,
            behavior=behavior,
            run=run,
        )
        await engine.close()
        await db.close()


@database_test
async def test_defaults_preserve_main_and_sync_never_exposes_credentials(desk):
    assert (await desk.registry.settings(desk.owner))["mode"] == "main"
    rows = await desk.registry.accounts(desk.owner)
    assert len(rows) == 2 and all(row["connected"] for row in rows)
    assert "secret" not in str(rows) and "ciphertext" not in str(rows) and "main-key" not in str(rows)
    assert await desk.engine.subaccounts.choose(await desk.run()) is None


@database_test
async def test_key_identity_and_nonmutating_access_probes(desk):
    with pytest.raises(AppError, match="another Delta account"):
        await desk.registry.save_key(desk.owner, desk.child, "main-key", "secret")
    with pytest.raises(AppError, match="not found"):
        await desk.registry.credentials("other-user", desk.child)
    await desk.registry.save_key(desk.owner, desk.child, "child-key", "secret")
    assert not any(method != "GET" for _, method, _, _ in desk.calls)


@database_test
async def test_only_one_run_can_reserve_an_exchange_account(desk):
    runs = [await desk.run(), await desk.run()]
    results = await asyncio.gather(
        *(desk.registry.reserve(desk.owner, run["id"], desk.child) for run in runs), return_exceptions=True
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, AppError) and result.code == "subaccount_busy" for result in results) == 1


@database_test
async def test_account_and_budget_are_immutable_and_wrong_journal_blocks(desk):
    run = await desk.run()
    await desk.registry.reserve(desk.owner, run["id"], desk.child)
    await desk.registry.set_budget(desk.owner, run["id"], Decimal(250), Decimal(1000))
    async with desk.db.pool.connection() as connection:
        with pytest.raises(RaiseException):
            await connection.execute(
                "update trade.account_assignments set account_id=%s where strategy_id=%s", (desk.second, run["id"])
            )
    with pytest.raises(AppError, match="disagrees"):
        await desk.engine.client_for_run({**run, "risk_state": {"exchangeAccount": f"india:{desk.parent}"}})
    await desk.registry.save_settings(desk.owner, "main", False, 0)
    client = await desk.engine.client_for_run(run)
    assert client.api_key == "child-key"
    await client.close()
    with pytest.raises(AppError, match="Finish subaccount"):
        await guard_subaccount_connections(desk.db, desk.owner)


@database_test
async def test_rotation_keeps_child_identity_and_does_not_mix_sessions(desk):
    clients = [
        await desk.engine.client_for_user(desk.owner),
        await desk.engine.client_for_user(desk.owner, exchange_account_id=desk.child),
    ]
    assert [client.api_key for client in clients] == ["main-key", "child-key"]
    assert [client.order_journal.account_id for client in clients] == [f"india:{desk.parent}", f"india:{desk.child}"]
    for client in clients:
        await client.close()
    await desk.engine.invalidate_credentials(desk.owner)
    assert not desk.engine.sessions


@database_test
async def test_pool_allocation_is_not_applied_twice(desk):
    desk.balances.update({"main-key": Decimal(500), "child-key": Decimal(250), "second-key": Decimal(250)})
    await desk.registry.save_settings(desk.owner, "automatic", True, 0)
    run = await desk.run()
    assigned = await desk.engine.subaccounts.choose(run)
    wallet, budget = await desk.engine.subaccounts.entry_budget(
        desk.owner, assigned, CapitalPolicy(allocation_mode="one_quarter_balance"), "BTC"
    )
    assert wallet == (Decimal(1000), Decimal(1000)) and budget == Decimal(250)
    await desk.engine.subaccounts.fund(desk.owner, assigned, budget, wallet[1])
    assert not any(method == "POST" for _, method, _, _ in desk.calls)


@database_test
async def test_transfer_timeout_is_not_replayed_and_blocks_family(desk):
    identity = uuid4()
    desk.behavior["transfer"] = "timeout"
    with pytest.raises(AppError, match="unknown"):
        await desk.engine.subaccounts.funding.transfer(desk.owner, identity, desk.parent, desk.child, Decimal(50))
    repeated = await desk.engine.subaccounts.funding.transfer(
        desk.owner, identity, desk.parent, desk.child, Decimal(50)
    )
    assert repeated["state"] == "unknown"
    assert sum(method == "POST" for _, method, _, _ in desk.calls) == 1
    with pytest.raises(AppError, match="pending"):
        await desk.engine.subaccounts.funding.transfer(desk.owner, uuid4(), desk.parent, desk.second, Decimal(10))
    with pytest.raises(AppError, match="pending"):
        await desk.registry.reserve(desk.owner, (await desk.run())["id"], desk.second)


@database_test
async def test_confirmed_funding_is_idempotent_and_preserves_pool_total(desk):
    identity = uuid4()
    funding = desk.engine.subaccounts.funding
    await funding.transfer(desk.owner, identity, desk.parent, desk.child, Decimal(250))
    await funding.transfer(desk.owner, identity, desk.parent, desk.child, Decimal(250))
    assert sum(method == "POST" for _, method, _, _ in desk.calls) == 1
    assert await desk.engine.subaccounts.wallet(desk.owner) == (Decimal(1000), Decimal(1000))
    assert desk.balances["child-key"] == 250


@database_test
async def test_auto_funding_reuses_idle_capital_without_touching_assigned_accounts(desk):
    desk.balances.update({"main-key": Decimal(0), "second-key": Decimal(1000)})
    await desk.registry.save_settings(desk.owner, "automatic", True, 0)
    assigned = await desk.registry.reserve(desk.owner, (await desk.run())["id"], desk.child)
    await desk.engine.subaccounts.fund(desk.owner, assigned, Decimal(250), Decimal(1000))
    assert desk.balances == {"main-key": Decimal(0), "child-key": Decimal(250), "second-key": Decimal(750)}
    posts = [body for _, method, _, body in desk.calls if method == "POST"]
    assert [(row["transferrer_user_id"], row["transferee_user_id"]) for row in posts] == [
        (desk.second, desk.parent),
        (desk.parent, desk.child),
    ]


@database_test
async def test_unknown_transfer_reconciles_from_exchange_history_without_resending(desk):
    identity = uuid4()
    desk.behavior["transfer"] = "timeout"
    with pytest.raises(AppError):
        await desk.engine.subaccounts.funding.transfer(desk.owner, identity, desk.parent, desk.child, Decimal(50))
    async with desk.db.pool.connection() as connection:
        cursor = await connection.execute(
            "select created_at,updated_at from trade.account_transfers where id=%s", (identity,)
        )
        created, updated = await cursor.fetchone()
    desk.behavior["history"] = [
        {
            "transferrer_user_id": desk.parent,
            "transferee_user_id": desk.child,
            "asset_symbol": "USD",
            "amount": "50",
            "created_at": (created + (updated - created) / 2).isoformat(),
        }
    ]
    result = await desk.engine.subaccounts.funding.reconcile(desk.owner, identity)
    assert result["state"] == "confirmed"
    assert sum(method == "POST" for _, method, _, _ in desk.calls) == 1


@database_test
async def test_funding_disabled_and_open_positions_block_transfers(desk):
    assigned = await desk.registry.reserve(desk.owner, (await desk.run())["id"], desk.child)
    with pytest.raises(AppError, match="enable automatic funding"):
        await desk.engine.subaccounts.fund(desk.owner, assigned, Decimal(250), Decimal(1000))
    desk.behavior["positions"] = [{"product_id": 123, "size": 1}]
    with pytest.raises(AppError, match="no positions"):
        await desk.engine.subaccounts.funding.transfer(desk.owner, uuid4(), desk.parent, desk.child, Decimal(50))
    assert not any(method == "POST" for _, method, _, _ in desk.calls)


@database_test
async def test_manual_mode_requires_assignment_and_pausing_keeps_exit_credentials(desk):
    await desk.registry.save_settings(desk.owner, "manual", False, 0)
    run = await desk.run()
    with pytest.raises(AppError, match="Assign a subaccount"):
        await desk.engine.subaccounts.choose(run)
    await desk.registry.reserve(desk.owner, run["id"], desk.child)
    await desk.registry.set_participation(desk.owner, desk.child, "disabled")
    client = await desk.engine.client_for_run(run)
    assert client.api_key == "child-key"
    await client.close()
    with pytest.raises(AppError, match="no longer enabled"):
        await desk.engine.subaccounts.entry_budget(
            desk.owner, await desk.registry.assignment(desk.owner, run["id"]), CapitalPolicy(), "BTC"
        )


@database_test
async def test_release_requires_exchange_flat_and_keeps_run_identity(desk):
    run = await desk.run()
    await desk.registry.reserve(desk.owner, run["id"], desk.child)
    with pytest.raises(AppError, match="Release"):
        await desk.registry.remove_key(desk.owner, desk.child)
    desk.behavior["orders"] = [{"id": 12, "product_id": 123}]
    with pytest.raises(AppError, match="no positions"):
        await desk.engine.subaccounts.release(desk.owner, run["id"])
    desk.behavior["orders"] = []
    await desk.engine.subaccounts.release(desk.owner, run["id"])
    assert (await desk.registry.assignment(desk.owner, run["id"]))["state"] == "released"
    assert (await desk.db.runtime.select("strategies", {"id": f"eq.{run['id']}"}))[0][
        "exchange_account_id"
    ] == desk.child


@database_test
async def test_profile_routes_require_user_and_do_not_return_secrets(desk, monkeypatch):
    from app import main
    from app.auth import require_user

    monkeypatch.setattr(main.app.state, "db", desk.db, raising=False)
    monkeypatch.setattr(main.app.state, "engine", desk.engine, raising=False)
    transport = httpx.ASGITransport(app=main.app)
    main.app.dependency_overrides[require_user] = lambda: {"id": desk.owner}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
            response = await client.get("/api/profile/subaccounts")
            assert response.status_code == 200, response.text
            assert response.json()["settings"]["mode"] == "main"
            assert "ciphertext" not in response.text and "child-key" not in response.text
            snapshot = await client.get(f"/api/profile/subaccounts/{desk.child}/snapshot")
            assert snapshot.status_code == 200, snapshot.text
            assert snapshot.json()["balance"] == "0"
            main.app.dependency_overrides.clear()
            unauthenticated = await client.get("/api/profile/subaccounts")
            assert unauthenticated.status_code == 401
            main.app.dependency_overrides[require_user] = lambda: {"id": desk.owner}
            bad = await client.put(
                "/api/profile/subaccounts/settings",
                json={"mode": "automatic", "autoFund": False, "expectedRevision": 99},
            )
            assert bad.status_code == 409
    finally:
        main.app.dependency_overrides.clear()
