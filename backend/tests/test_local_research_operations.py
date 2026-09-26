"""Verify research decisions commit atomically in the local PostgreSQL store."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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


@pytest.mark.asyncio
async def test_shared_decision_publish_and_recheck_commit_once():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        saved_id, run_id, snapshot_id = (str(uuid4()) for _ in range(3))
        now = datetime.now(UTC)
        activation = (now + timedelta(minutes=20)).isoformat()
        exit_at = (now + timedelta(minutes=30)).isoformat()
        expiry = (now + timedelta(minutes=35)).isoformat()
        template = {
            "name": "Research spread",
            "enabledForAi": True,
            "entry": {"strategyType": "intraday", "entryAt": now.isoformat(), "exitAt": exit_at},
            "legs": [{"id": "short", "position": "sell", "expiry": "2026-09-25"}],
        }
        proposal = {**template, "entry": {**template["entry"], "entryAt": activation}}
        async with pool.connection() as db:
            await db.execute(
                """insert into trade.saved_strategies
                   (id,user_id,name,definition_json,enabled_for_ai,version,created_at,updated_at)
                   values (%s,null,%s,%s,true,1,%s,%s)""",
                (saved_id, "Research spread", Jsonb(template), now, now),
            )
        await runtime.write(
            "automation_agent_runs",
            {
                "id": run_id,
                "user_id": "global",
                "status": "running",
                "trigger": "manual",
                "run_key": f"research:{run_id}",
                "market_snapshot_id": snapshot_id,
                "scheduled_for": now.isoformat(),
            },
        )
        args = {
            "runId": run_id,
            "candidates": [{"id": saved_id, "version": 1}],
            "activation": activation,
            "expiry": expiry,
            "exit": exit_at,
            "definitionJson": json.dumps(proposal),
            "confidence": 0.8,
            "reasoning": "Evidence supports a bounded trade",
            "supporting": [],
            "invalidation": [],
            "snapshotId": snapshot_id,
        }
        published = await runtime.data.request("sharedAnalysis:publish", args, mutation=True)
        assert published["outcome"] == "strategy_selected"
        with pytest.raises(AppError):
            await runtime.data.request("sharedAnalysis:publish", args, mutation=True)
        recheck_id = published["activationRecheckRunId"]
        await runtime.update("automation_agent_runs", {"status": "running"}, {"id": f"eq.{recheck_id}"})
        rechecked = await runtime.data.request(
            "runtimeAutomation:recheck",
            {
                "userId": "global",
                "runId": recheck_id,
                "proposalId": published["proposalId"],
                "drop": False,
            },
            mutation=True,
        )
        assert rechecked["outcome"] == "strategy_reconfirmed"


@pytest.mark.asyncio
async def test_account_schedule_creates_strategy_proposal_and_recheck_atomically():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        user_id, saved_id, run_id, snapshot_id = (str(uuid4()) for _ in range(4))
        now = datetime.now(UTC)
        activation = (now + timedelta(minutes=20)).isoformat()
        recheck = (now + timedelta(minutes=13)).isoformat()
        exit_at = (now + timedelta(minutes=30)).isoformat()
        expiry = (now + timedelta(minutes=35)).isoformat()
        template = {
            "name": "Account spread",
            "enabledForAi": True,
            "entry": {"strategyType": "intraday", "entryAt": now.isoformat(), "exitAt": exit_at},
            "legs": [{"id": "short", "position": "sell", "expiry": "2026-09-25"}],
        }
        proposal = {**template, "entry": {**template["entry"], "entryAt": activation}}
        async with pool.connection() as db:
            await db.execute(
                """insert into trade.users (user_id,connection,automation,capital,record)
                   values (%s,%s,%s,%s,%s)""",
                (
                    user_id,
                    Jsonb({"status": "connected", "delta_user_id": str(uuid4())}),
                    Jsonb({"enabled": True}),
                    Jsonb({"allocation_mode": "half_balance"}),
                    Jsonb({"userId": user_id}),
                ),
            )
            await db.execute(
                """insert into trade.saved_strategies
                   (id,user_id,name,definition_json,enabled_for_ai,version,created_at,updated_at)
                   values (%s,null,%s,%s,true,1,%s,%s)""",
                (saved_id, "Account spread", Jsonb(template), now, now),
            )
        await runtime.write(
            "automation_agent_runs",
            {
                "id": run_id,
                "user_id": user_id,
                "status": "running",
                "trigger": "manual",
                "run_key": f"account:{run_id}",
                "market_snapshot_id": snapshot_id,
                "scheduled_for": now.isoformat(),
            },
        )
        args = {
            "userId": user_id,
            "runId": run_id,
            "savedId": saved_id,
            "savedVersion": 1,
            "activation": activation,
            "expiry": expiry,
            "exit": exit_at,
            "recheck": recheck,
            "definitionJson": json.dumps(proposal),
            "confidence": 0.75,
            "reasoning": "A bounded setup",
            "supporting": [],
            "invalidation": [],
            "snapshotId": snapshot_id,
            "newsId": None,
        }
        result = await runtime.data.request("runtimeAutomation:schedule", args, mutation=True)
        assert result["outcome"] == "strategy_selected"
        assert (await runtime.select("strategies", {"id": f"eq.{result['scheduledStrategyId']}"}))[0][
            "status"
        ] == "scheduled"
        assert (await runtime.select("automation_agent_runs", {"id": f"eq.{result['activationRecheckRunId']}"}))[0][
            "trigger"
        ] == "activation_recheck"
        with pytest.raises(AppError):
            await runtime.data.request("runtimeAutomation:schedule", args, mutation=True)

        # The recheck may drop only the strategy bound to it, identified by name and activation time.
        recheck_id = result["activationRecheckRunId"]
        await runtime.update("automation_agent_runs", {"status": "running"}, {"id": f"eq.{recheck_id}"})
        drop = {
            "userId": user_id, "runId": recheck_id, "proposalId": result["proposalId"], "drop": True,
            "name": "Account spread", "activation": activation, "reason": "Range broke",
        }
        for mismatch in ({"name": "Long call"}, {"activation": (now + timedelta(minutes=21)).isoformat()}):
            with pytest.raises(AppError):
                await runtime.data.request("runtimeAutomation:recheck", {**drop, **mismatch}, mutation=True)
        dropped = await runtime.data.request("runtimeAutomation:recheck", drop, mutation=True)
        assert dropped["outcome"] == "strategy_dropped"
        assert (await runtime.select("strategies", {"id": f"eq.{result['scheduledStrategyId']}"}))[0][
            "status"
        ] == "cancelled"


@pytest.mark.asyncio
async def test_followup_records_its_parent_and_child_context_returns_it():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        user_id, run_id, snapshot_id = (str(uuid4()) for _ in range(3))
        now = datetime.now(UTC)
        async with pool.connection() as db:
            await db.execute(
                """insert into trade.users (user_id,connection,automation,capital,record)
                   values (%s,null,%s,%s,%s)""",
                (
                    user_id,
                    Jsonb({"enabled": True, "minimum_follow_up_minutes": 5, "maximum_agent_runs_per_day": 3}),
                    Jsonb({"allocation_mode": "half_balance"}),
                    Jsonb({"userId": user_id}),
                ),
            )
        await runtime.write(
            "automation_agent_runs",
            {
                "id": run_id, "user_id": user_id, "status": "running", "trigger": "asia_session",
                "run_key": f"asia:{run_id}", "scheduled_for": now.isoformat(),
            },
        )
        follow_up_at = now + timedelta(minutes=20)
        result = await runtime.data.request(
            "runtimeAutomation:followup",
            {
                "userId": user_id, "runId": run_id, "next": follow_up_at.isoformat(),
                "reason": "Wait for the breakout to confirm", "signals": ["volume"],
                "fixed": (now + timedelta(hours=2)).isoformat(),
                "previous": (now - timedelta(hours=2)).isoformat(),
                "dayStart": (now - timedelta(hours=12)).isoformat(),
                "dayEnd": (now + timedelta(hours=12)).isoformat(),
                "snapshotId": snapshot_id, "newsId": None,
            },
            mutation=True,
        )
        assert result["outcome"] == "wait_and_run_again"
        child = (await runtime.select("automation_agent_runs", {"id": f"eq.{result['scheduledRunId']}"}))[0]
        assert child["parent_agent_run_id"] == run_id
        assert child["market_snapshot_id"] == snapshot_id
        context = await runtime.data.request("runtimeAutomation:context", {"userId": user_id, "runId": child["id"]})
        assert context["parent"]["id"] == run_id
        assert context["parent"]["outcome"] == "wait_and_run_again"
        with pytest.raises(AppError):
            await runtime.data.request(
                "runtimeAutomation:context", {"userId": str(uuid4()), "runId": child["id"]}
            )
