"""Owner Users and personal P&L endpoints against real PostgreSQL and a stub Supabase REST API."""

import asyncio
import json
import os
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.database import Database
from app.engine import TradingEngine
from app.errors import AppError
from tests.test_owner_ledger import settled_run

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)
SETTINGS = SimpleNamespace(
    supabase_url="https://supabase.test",
    supabase_publishable_key="publishable",
    supabase_service_role_key="service-role-secret",
    analysis_service_secret="test-analysis-service-secret",
    chart_link_seconds=3600,
    auth_profile_cache_seconds=60,
    trading_writer_enabled=True,
)
FORBIDDEN = ("ciphertext", "fingerprint", "api_key", "api_secret", "service-role-secret", "access_token",
             "refresh_token", "encrypted", "raw exchange body", '"response"')


@pytest.fixture(scope="module")
def event_loop_policy():
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


def supabase(profiles: list[dict], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        params = request.url.params
        rows = sorted(profiles, key=lambda row: (row["user_type"], row["created_at"], row["id"]))
        if "id" in params:
            rows = [row for row in rows if row["id"] == params["id"][3:]]
        if "or" in params:
            needle = params["or"].split("ilike.*", 1)[1].split("*", 1)[0].lower()
            rows = [row for row in rows if needle in (row["display_name"] + row["email"]).lower()]
        total = len(rows)
        offset = int(params.get("offset", "0"))
        rows = rows[offset: offset + int(params.get("limit", "100"))]
        columns = params["select"].split(",")
        body = [{column: row.get(column) for column in columns} for row in rows]
        return httpx.Response(200, json=body, headers={"content-range": f"0-{len(body)}/{total}"})

    return httpx.MockTransport(handle)


class Engine:
    """Delta stand-in: the owner's wallet answers, the brother's exchange call fails."""

    def __init__(self, owner_id: str, db: Database) -> None:
        self.owner_id = owner_id
        self.application_data = db.local_data

    async def save_capital_policy(self, user_id, mode, amount, *, actor_id=None):
        # The real engine method, writing through the real local store.
        await TradingEngine.save_capital_policy(self, user_id, mode, amount, actor_id=actor_id)

    async def client_for_user(self, user_id: str):
        if user_id != self.owner_id:
            raise AppError(502, "Delta request failed", "delta_request_failed")
        return SimpleNamespace(close=lambda: asyncio.sleep(0))

    async def usd_capital(self, client):
        return Decimal("80"), Decimal("120")

    async def run_detail(self, run_id, user_id, *, include_raw=True):
        raise AppError(404, "Strategy not found", "strategy_not_found")


async def add_account(pool, user_id: str, *, connected: bool, enabled: bool) -> None:
    connection = {"status": "connected" if connected else "revoked", "delta_user_id": str(uuid4()),
                  "account_name": "Main", "email_masked": "ow**@example.com", "ciphertext": "encrypted-secret",
                  "fingerprint": "fp"}
    async with pool.connection() as db:
        await db.execute(
            """insert into trade.users (user_id,connection,automation,capital,record) values (%s,%s,%s,%s,%s)""",
            (user_id, Jsonb(connection), Jsonb({"enabled": enabled, "model_id": "m", "minimum_follow_up_minutes": 5,
                                                "maximum_agent_runs_per_day": 3}),
             Jsonb({"allocation_mode": "half_balance", "capital_amount": None}), Jsonb({"userId": user_id})),
        )


@pytest.fixture
async def api(monkeypatch):
    from app import main

    owner, brother, newcomer = str(uuid4()), str(uuid4()), str(uuid4())
    profiles = [
        {"id": owner, "email": "owner@example.com", "display_name": "Owner", "phone_number": None,
         "avatar_url": None, "user_type": "owner", "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": brother, "email": "brother@example.com", "display_name": "Brother", "phone_number": "+911234567890",
         "avatar_url": None, "user_type": "user", "created_at": "2026-02-01T00:00:00+00:00"},
        {"id": newcomer, "email": "new@example.com", "display_name": "Newcomer", "phone_number": None,
         "avatar_url": None, "user_type": "user", "created_at": "2026-09-01T00:00:00+00:00"},
    ]
    seen: list[httpx.Request] = []
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        db = Database(SETTINGS, pool)  # type: ignore[arg-type]
        await db.client.aclose()
        db.client = httpx.AsyncClient(transport=supabase(profiles, seen))
        await add_account(pool, owner, connected=True, enabled=True)
        await add_account(pool, brother, connected=True, enabled=True)
        current = {"id": owner}
        main.app.state.db = db
        main.app.state.engine = Engine(owner, db)
        main.app.state.wallet_probe = None
        monkeypatch.setattr(main, "settings", SimpleNamespace(trading_writer_enabled=True))
        main.app.dependency_overrides[main.require_user] = lambda: dict(current)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test.local"
            ) as client:
                yield SimpleNamespace(client=client, db=db, owner=owner, brother=brother, newcomer=newcomer,
                                      current=current, seen=seen)
        finally:
            main.app.dependency_overrides.clear()
            await db.close()


async def test_owner_sees_every_registered_user_including_themself(api):
    await settled_run(api.db.runtime, api.brother)
    response = await api.client.get("/api/owner/users")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["registeredUsers"] == 3
    ids = [item["id"] for item in body["items"]]
    assert ids[0] == api.owner and set(ids) == {api.owner, api.brother, api.newcomer}
    me = body["items"][0]
    assert me["isCurrentUser"] and me["userType"] == "owner" and me["automationEnabled"]
    brother = next(item for item in body["items"] if item["id"] == api.brother)
    assert brother["performance"]["settledRuns"] == 1
    newcomer = next(item for item in body["items"] if item["id"] == api.newcomer)
    assert newcomer["account"] == {"initialized": False, "connectionStatus": "not_connected", "accountName": None,
                                   "email": None, "deltaAccountId": None}
    assert newcomer["lastRecordedWallet"] is None
    assert not any(word in response.text for word in FORBIDDEN)


async def test_search_is_sanitized_before_reaching_postgrest(api):
    response = await api.client.get("/api/owner/users", params={"search": "bro,ther)*"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [api.brother]
    assert response.json()["summary"]["registeredUsers"] == 3
    filters = [request.url.params["or"] for request in api.seen if "or" in request.url.params]
    assert filters == ["(display_name.ilike.*brother*,email.ilike.*brother*)"]
    assert (await api.client.get("/api/owner/users", params={"limit": 500})).status_code == 400


async def test_ordinary_user_cannot_call_owner_endpoints(api):
    api.current["id"] = api.brother
    for method, path in (
        ("GET", "/api/owner/users"),
        ("GET", f"/api/owner/users/{api.owner}"),
        ("GET", f"/api/owner/users/{api.brother}/trades"),
        ("GET", f"/api/owner/users/{api.brother}/trades/{uuid4()}"),
        ("PUT", f"/api/owner/users/{api.brother}/automation"),
        ("PUT", f"/api/owner/users/{api.owner}/capital"),
    ):
        body = {"enabled": True} if path.endswith("automation") else {"allocationMode": "full_balance"}
        response = await api.client.request(method, path, json=body if method == "PUT" else None)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "owner_required"


async def test_deleted_run_leaves_personal_pnl_but_stays_marked_for_owner(api):
    runtime = api.db.runtime
    kept = await settled_run(runtime, api.brother, exit_price="400")
    removed = await settled_run(runtime, api.brother, exit_price="1500")
    await runtime.update("strategies", {}, {"id": f"eq.{removed}"}, remove=True)

    api.current["id"] = api.brother
    personal = (await api.client.get("/api/me/pnl")).json()
    assert personal["scope"] == "personal" and personal["summary"]["settledRuns"] == 1
    assert personal["summary"]["netRealizedPnl"] == "0.4000000000"
    trades = (await api.client.get("/api/me/trades")).json()
    assert [item["runId"] for item in trades["items"]] == [kept]
    assert removed not in json.dumps(trades)

    api.current["id"] = api.owner
    owner_trades = (await api.client.get(f"/api/owner/users/{api.brother}/trades")).json()
    marked = {item["runId"]: item["deletedByUserAt"] for item in owner_trades["items"]}
    assert marked[kept] is None and marked[removed] is not None
    detail = await api.client.get(f"/api/owner/users/{api.brother}/trades/{removed}")
    assert detail.status_code == 200 and detail.json()["source"] == "archive"
    assert len(detail.json()["run"]["orders"]) == 2
    assert not any(word in detail.text for word in FORBIDDEN)
    user = (await api.client.get(f"/api/owner/users/{api.brother}")).json()
    assert user["performance"]["ownerScope"]["settledRuns"] == 2
    assert user["performance"]["userScope"]["settledRuns"] == 1


async def test_owner_own_personal_pnl_excludes_other_users(api):
    await settled_run(api.db.runtime, api.brother)
    body = (await api.client.get("/api/me/pnl", params={"range": "30d"})).json()
    assert body["summary"]["totalRuns"] == 0
    assert (await api.client.get("/api/me/pnl", params={"range": "5y"})).status_code == 400


async def test_personal_asset_filter_matches_totals_and_paginated_chart_trades(api):
    runtime = api.db.runtime
    legacy = await settled_run(runtime, api.brother)
    btc_loss = await settled_run(runtime, api.brother, asset="BTC", exit_price="1500")
    eth_win = await settled_run(runtime, api.brother, asset="ETH")
    eth_loss = await settled_run(runtime, api.brother, asset="ETH", exit_price="2000")
    removed = await settled_run(runtime, api.brother, asset="ETH")
    await runtime.update("strategies", {}, {"id": f"eq.{removed}"}, remove=True)
    await settled_run(runtime, api.owner, asset="ETH")
    api.current["id"] = api.brother

    for asset, ids, net in (
        (None, {legacy, btc_loss, eth_win, eth_loss}, "-1.5"),
        ("BTC", {legacy, btc_loss}, "-0.3"),
        ("ETH", {eth_win, eth_loss}, "-1.2"),
    ):
        params = {"range": "30d"}
        if asset:
            params["asset"] = asset
        response = await api.client.get("/api/me/pnl", params=params)
        assert response.status_code == 200
        summary = response.json()["summary"]
        assert Decimal(summary["netRealizedPnl"]) == Decimal(net)
        assert summary["wins"] == summary["losses"] == len(ids) // 2
        assert summary["settledRuns"] == summary["totalRuns"] == len(ids)
        assert Decimal(summary["grossGains"]) + Decimal(summary["grossLosses"]) == Decimal(net)
        assert Decimal(summary["exchangeFees"]) == Decimal("0.2") * len(ids)
        assert summary["winRate"] == 0.5

        items = []
        query = {**params, "state": "settled", "limit": 1}
        while True:
            page = await api.client.get("/api/me/trades", params=query)
            assert page.status_code == 200
            body = page.json()
            items.extend(body["items"])
            if not body["nextCursor"]:
                break
            query["cursor"] = body["nextCursor"]
        assert {item["runId"] for item in items} == ids
        assert sum(Decimal(item["realizedPnl"]) for item in items) == Decimal(net)


async def test_personal_asset_filter_rejects_unknown_assets_and_handles_no_matches(api):
    await settled_run(api.db.runtime, api.owner, asset="BTC")
    summary = (await api.client.get("/api/me/pnl", params={"asset": "ETH"})).json()["summary"]
    assert summary["settledRuns"] == summary["totalRuns"] == 0
    assert Decimal(summary["netRealizedPnl"]) == 0 and summary["winRate"] is None
    trades = (await api.client.get("/api/me/trades", params={"asset": "ETH"})).json()
    assert trades["items"] == [] and trades["nextCursor"] is None
    for path in ("/api/me/pnl", "/api/me/trades"):
        assert (await api.client.get(path, params={"asset": "SOL"})).status_code == 400


async def test_capital_shows_live_unavailable_and_recorded_values_separately(api):
    run_id = await settled_run(api.db.runtime, api.brother)
    owner = (await api.client.get(f"/api/owner/users/{api.owner}")).json()
    assert owner["wallet"]["state"] == "live" and owner["wallet"]["totalBalance"] == "120"
    assert any(item["source"] == "live_wallet" for item in owner["capitalHistory"])

    brother = (await api.client.get(f"/api/owner/users/{api.brother}")).json()
    assert brother["wallet"] == {"state": "unavailable", "reason": "delta_request_failed", "observedAt": None}
    entry = [item for item in brother["capitalHistory"] if item["runId"] == run_id]
    kinds = {item["kind"]: item for item in entry}
    # The entry budget is an allocation, the entry wallet is a separate wallet observation.
    assert kinds["run_allocation"]["allocatedBudget"] == "50.0000000000"
    assert kinds["wallet"]["totalBalance"] == "100.0000000000"

    newcomer = (await api.client.get(f"/api/owner/users/{api.newcomer}")).json()
    assert newcomer["wallet"] == {"state": "not_connected"} and newcomer["capitalHistory"] == []
    assert (await api.client.get(f"/api/owner/users/{uuid4()}")).status_code == 404


async def test_owner_switches_automation_for_any_account_including_their_own(api):
    run_id = str(uuid4())
    await api.db.runtime.write("strategies", {"id": run_id, "user_id": api.brother, "name": "Agent", "status":
                                              "scheduled"})
    await api.db.runtime.write("strategy_proposals", {"user_id": api.brother, "strategy_id": run_id,
                                                      "status": "scheduled"})
    off = await api.client.put(f"/api/owner/users/{api.brother}/automation", json={"enabled": False})
    assert off.status_code == 200 and off.json()["automation"] == {"enabled": False}
    assert (await api.db.runtime.select("strategies", {"id": f"eq.{run_id}"}))[0]["status"] == "cancelled"
    mine = await api.client.put(f"/api/owner/users/{api.owner}/automation", json={"enabled": False})
    assert mine.json()["automation"] == {"enabled": False}
    detail = (await api.client.get(f"/api/owner/users/{api.brother}")).json()
    assert detail["automation"] == {"enabled": False}
    missing = await api.client.put(f"/api/owner/users/{uuid4()}/automation", json={"enabled": True})
    assert missing.status_code == 404


async def test_owner_sets_per_strategy_budget_for_any_account(api):
    fixed = await api.client.put(
        f"/api/owner/users/{api.brother}/capital", json={"allocationMode": "fixed_amount", "capitalAmount": 75}
    )
    assert fixed.status_code == 200
    assert fixed.json()["capitalPolicy"] == {"allocationMode": "fixed_amount", "capitalAmount": "75.0"}
    detail = (await api.client.get(f"/api/owner/users/{api.brother}")).json()
    assert detail["capitalPolicy"]["allocationMode"] == "fixed_amount"
    assert any(item["source"] == "capital_policy" for item in detail["capitalHistory"])
    async with api.db.pool.connection() as connection:
        audit = await (await connection.execute(
            """select actor_user_id,old_allocation_mode,new_allocation_mode,new_capital_amount
               from owner_reporting.capital_policy_changes where target_user_id=%s""",
            (api.brother,),
        )).fetchall()
    assert audit == [(api.owner, "half_balance", "fixed_amount", Decimal("75"))]

    # The owner's own account uses the same control, and the preview follows the live wallet.
    mine = await api.client.put(f"/api/owner/users/{api.owner}/capital", json={"allocationMode": "one_quarter_balance"})
    assert mine.status_code == 200
    preview = (await api.client.get(f"/api/owner/users/{api.owner}")).json()["budgetPreview"]
    assert preview == {"budgetPerStrategy": "30", "nextStrategyCanUse": "30", "maximumConcurrentStrategies": 4}

    missing_amount = await api.client.put(f"/api/owner/users/{api.brother}/capital",
                                          json={"allocationMode": "fixed_amount"})
    assert missing_amount.status_code == 400
    unknown = await api.client.put(f"/api/owner/users/{uuid4()}/capital", json={"allocationMode": "half_balance"})
    assert unknown.status_code == 404
