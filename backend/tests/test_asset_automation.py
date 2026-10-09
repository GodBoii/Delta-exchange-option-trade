"""Owner asset pauses survive reload and protect the other asset and live trades."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
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
