"""Personal asset controls and owner pauses preserve account isolation and live trades."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from app import automation
from app.assets import automation_asset_enabled
from app.errors import AppError
from app.local_runtime import LocalRuntimeStore


def test_legacy_settings_keep_both_assets_enabled():
    assert automation_asset_enabled({"enabled": True}, "BTC")
    assert automation_asset_enabled({"enabled": True}, "ETH")
    assert not automation_asset_enabled({"enabled": False}, "ETH")
    settings = {"enabled": True, "asset_enabled": {"ETH": False}}
    assert automation_asset_enabled(settings, "BTC")
    assert not automation_asset_enabled(settings, "ETH")


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_projected_legacy_settings_allow_null_asset_flags(asset):
    assert automation_asset_enabled({"asset_enabled": None}, asset)
    assert not automation_asset_enabled({"enabled": False, "asset_enabled": None}, asset)


async def test_asset_endpoint_rejects_regular_accounts_before_writing():
    mutation = AsyncMock()
    db = SimpleNamespace(profile=AsyncMock(return_value={"user_type": "user"}),
                         runtime=SimpleNamespace(data=SimpleNamespace(request=mutation)))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
    with pytest.raises(AppError) as caught:
        await automation.update_asset_automation(
            "ETH", request, automation.AssetAutomationUpdate(enabled=False), {"id": "regular-user"},
        )
    assert caught.value.status == 403
    mutation.assert_not_awaited()


def test_asset_switch_requires_a_boolean():
    for value in ("false", 0, None):
        with pytest.raises(ValidationError):
            automation.AssetAutomationUpdate(enabled=value)


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_personal_asset_endpoint_targets_authenticated_account(monkeypatch, asset, enabled):
    mutation = AsyncMock(return_value={"enabled": False, "asset_enabled": {asset: enabled}})
    db = SimpleNamespace(runtime=SimpleNamespace(data=SimpleNamespace(request=mutation)))
    initialize = AsyncMock()
    monkeypatch.setattr(automation, "ensure_settings", initialize)
    app = FastAPI()
    app.state.db = db
    app.include_router(automation.router)
    app.dependency_overrides[automation.require_user] = lambda: {"id": "regular-user"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"/api/automation/settings/assets/{asset}", json={"enabled": enabled})
        assert response.status_code == 200
        assert response.json()["assetEnabled"] == {"BTC": True, "ETH": True, asset: enabled}
        initialize.assert_awaited_once_with(db, "regular-user")
        mutation.assert_awaited_once_with(
            "accountAutomation:setAssetEnabled",
            {"userId": "regular-user", "asset": asset, "enabled": enabled}, mutation=True,
        )
        mutation.reset_mock()
        for payload in ({"enabled": "false"}, {"enabled": 0}, {"enabled": None},
                        {"enabled": False, "userId": "other-user"}):
            invalid = await client.put(f"/api/automation/settings/assets/{asset}", json=payload)
            assert invalid.status_code == 422
        invalid = await client.put("/api/automation/settings/assets/SOL", json={"enabled": False})
        assert invalid.status_code == 422
        mutation.assert_not_awaited()


@pytest.mark.parametrize("flags", [None, {"BTC": False, "ETH": True}])
async def test_overview_returns_personal_preferences_even_when_master_is_paused(monkeypatch, flags):
    settings = {"enabled": False, "asset_enabled": flags}
    monkeypatch.setattr(automation, "ensure_settings", AsyncMock(return_value=settings))
    db = SimpleNamespace(
        select=AsyncMock(return_value=[]), charts=SimpleNamespace(available=AsyncMock(return_value=set())),
    )
    engine = SimpleNamespace(
        capital_policy=AsyncMock(return_value=SimpleNamespace(concurrency_limit=lambda: None)),
        saved_strategies=AsyncMock(return_value=[]),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, engine=engine)))
    result = await automation.automation_overview(request, {"id": "regular-user"})
    assert result["settings"]["assetEnabled"] == (flags or {"BTC": True, "ETH": True})
    assert result["settings"]["enabled"] is False
    assert result["assetAutomation"] == {"BTC": False, "ETH": False}


async def test_personal_asset_endpoint_requires_sign_in():
    app = FastAPI()
    app.include_router(automation.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(AppError) as caught:
            await client.put("/api/automation/settings/assets/ETH", json={"enabled": False})
    assert caught.value.status == 401


@pytest.fixture
async def asset_runtime():
    url = os.getenv("TEST_LOCAL_DATABASE_URL")
    if not url:
        pytest.skip("No isolated local PostgreSQL test URL")
    async with AsyncConnectionPool(url, open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        actor = str(uuid4())
        async with pool.connection() as connection:
            result = await connection.execute("select owner_user_id,analysis from trade.system_settings")
            previous = await result.fetchone()
            await connection.execute(
                """insert into trade.system_settings (key,owner_user_id,analysis) values ('main',%s,%s)
                   on conflict (key) do update set owner_user_id=excluded.owner_user_id,analysis=excluded.analysis""",
                (actor, Jsonb({"enabled": True, "minimum_follow_up_minutes": 5, "maximum_agent_runs_per_day": 3})),
            )
        try:
            yield runtime, actor
        finally:
            async with pool.connection() as connection:
                if previous:
                    await connection.execute(
                        "update trade.system_settings set owner_user_id=%s,analysis=%s where key='main'",
                        (previous[0], Jsonb(previous[1])),
                    )
                else:
                    await connection.execute("delete from trade.system_settings where key='main'")


async def test_pause_cancels_only_selected_asset_and_persists(asset_runtime):
    runtime, actor = asset_runtime
    now = datetime.now(UTC).isoformat()
    identifiers = {}
    for asset in ("BTC", "ETH"):
        for status in ("scheduled", "active"):
            strategy_id = str(uuid4())
            identifiers[asset, status] = strategy_id
            await runtime.write("strategies", {
                "id": strategy_id, "user_id": actor, "status": status, "asset": asset,
                "definition_json": {"instrument": {"underlying": asset}},
            })
            await runtime.write("strategy_proposals", {
                "id": str(uuid4()), "user_id": actor, "status": "scheduled", "asset": asset,
                "strategy_id": strategy_id,
            })
        for status in ("scheduled", "running"):
            job_id = str(uuid4())
            identifiers[asset, status, "job"] = job_id
            await runtime.write("automation_agent_runs", {
                "id": job_id, "user_id": "global", "status": status, "asset": asset,
                "scheduled_for": now, "trigger": "manual",
            })
    manual_id = str(uuid4())
    await runtime.write("strategies", {
        "id": manual_id, "user_id": actor, "status": "scheduled",
        "definition_json": {"instrument": {"underlying": "ETH"}},
    })
    flags = await runtime.data.request("sharedAnalysis:setAssetEnabled", {
        "actorId": actor, "asset": "ETH", "enabled": False,
    }, mutation=True)
    assert flags == {"BTC": True, "ETH": False}
    settings = await LocalRuntimeStore(runtime.pool).select("automation_settings", {"user_id": "eq.global"})
    assert settings[0]["asset_enabled"] == {"ETH": False}
    for asset in ("BTC", "ETH"):
        for status in ("scheduled", "active"):
            row = (await runtime.select("strategies", {"id": f"eq.{identifiers[asset, status]}"}))[0]
            assert row["status"] == ("cancelled" if asset == "ETH" and status == "scheduled" else status)
        for status in ("scheduled", "running"):
            row = (await runtime.select("automation_agent_runs", {"id": f"eq.{identifiers[asset, status, 'job']}"}))[0]
            assert row["status"] == ("cancelled" if asset == "ETH" else status)
    assert (await runtime.select("strategies", {"id": f"eq.{manual_id}"}))[0]["status"] == "scheduled"
    # A running analyzer cannot write another action after its owner pauses the asset.
    running_id = identifiers["ETH", "running", "job"]
    with pytest.raises(AppError) as caught:
        await runtime.data.request("runtimeAutomation:saveSnapshot", {
            "userId": "global", "runId": running_id, "snapshotId": str(uuid4()),
        }, mutation=True)
    assert caught.value.code == "automation_asset_paused"
    # Resuming schedules future sessions; cancelled entries remain cancelled.
    await runtime.data.request("sharedAnalysis:setAssetEnabled", {
        "actorId": actor, "asset": "ETH", "enabled": True,
    }, mutation=True)
    row = (await runtime.select("strategies", {"id": f"eq.{identifiers['ETH', 'scheduled']}"}))[0]
    assert row["status"] == "cancelled"
    with pytest.raises(AppError) as caught:
        await runtime.data.request("runtimeAutomation:saveSnapshot", {
            "userId": "global", "runId": running_id, "snapshotId": str(uuid4()),
        }, mutation=True)
    assert caught.value.code == "run_not_active"


async def test_pause_blocks_manual_requests_and_stale_scheduler_claims(asset_runtime):
    runtime, actor = asset_runtime
    await runtime.data.request("sharedAnalysis:setAssetEnabled", {
        "actorId": actor, "asset": "BTC", "enabled": False,
    }, mutation=True)
    with pytest.raises(AppError) as caught:
        await runtime.data.request("sharedAnalysis:manual", {"requestedBy": actor, "asset": "BTC"}, mutation=True)
    assert caught.value.code == "automation_asset_paused"
    assert (await runtime.data.request(
        "sharedAnalysis:manual", {"requestedBy": actor, "asset": "ETH"}, mutation=True,
    ))["asset"] == "ETH"
    job = {"id": str(uuid4()), "user_id": "global", "status": "scheduled", "trigger": "manual",
           "scheduled_for": datetime.now(UTC).isoformat()}
    # Missing asset is a legacy BTC row. It must not get through a stale scheduler read.
    await runtime.write("automation_agent_runs", job)
    with pytest.raises(AppError) as caught:
        await runtime.rpc("claim_automation_agent_run", {"p_user_id": "global", "p_run_id": job["id"]})
    assert caught.value.code == "automation_asset_paused"
    created = await runtime.rpc("ensure_automation_fixed_runs", {"p_runs": [{
        **job, "run_key": f"pause-test:{uuid4()}",
        "scheduled_for": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }]})
    assert created == 0


async def test_only_owner_can_pause_assets(asset_runtime):
    runtime, _ = asset_runtime
    with pytest.raises(AppError) as caught:
        await runtime.data.request("sharedAnalysis:setAssetEnabled", {
            "actorId": str(uuid4()), "asset": "ETH", "enabled": False,
        }, mutation=True)
    assert caught.value.status == 403
    settings = await runtime.select("automation_settings", {"user_id": "eq.global"})
    assert "asset_enabled" not in settings[0]
