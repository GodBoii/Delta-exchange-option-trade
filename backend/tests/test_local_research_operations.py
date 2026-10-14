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
from app.exit_schedule import session_expiry
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
        now = (datetime.now(UTC) + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
        activation = (now + timedelta(minutes=20)).isoformat()
        exit_at = (now + timedelta(minutes=30)).isoformat()
        expiry = (now + timedelta(minutes=35)).isoformat()
        template = {
            "name": "Research spread",
            "instrument": {"underlying": "BTC"},
            "enabledForAi": True,
            "entry": {"strategyType": "intraday", "entryAt": now.isoformat(), "exitAt": exit_at},
            "legs": [{"id": "short", "position": "sell", "expiry": session_expiry(now).date().isoformat()}],
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
                "name": "Research spread",
                "activation": activation,
                "reason": "Fresh evidence confirms eligibility",
            },
            mutation=True,
        )
        assert rechecked["outcome"] == "strategy_reconfirmed"


@pytest.mark.parametrize("shared", [True, False])
@pytest.mark.parametrize("asset", ["BTC", "ETH"])
@pytest.mark.parametrize("drop", [True, False])
async def test_explicit_recheck_decision_is_assigned_and_commits_once(shared, asset, drop):
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        user_id = "global" if shared else str(uuid4())
        run_id, proposal_id, strategy_id = (str(uuid4()) for _ in range(3))
        activation = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        if not shared:
            async with pool.connection() as connection:
                await connection.execute(
                    "insert into trade.users (user_id,automation,capital,record) values (%s,%s,%s,%s)",
                    (user_id, Jsonb({"enabled": True}), Jsonb({}), Jsonb({})),
                )
            await runtime.write("strategies", {
                "id": strategy_id, "user_id": user_id, "status": "scheduled", "asset": asset,
                "name": "Assigned strategy", "entry_at": activation,
                "definition_json": {"instrument": {"underlying": asset}},
            })
        await runtime.write("strategy_proposals", {
            "id": proposal_id, "user_id": user_id, "status": "scheduled", "asset": asset,
            "name": "Assigned strategy", "activation_time": activation,
            **({"strategy_id": strategy_id} if not shared else {}),
        })
        await runtime.write("automation_agent_runs", {
            "id": run_id, "user_id": user_id, "status": "running", "asset": asset,
            "trigger": "activation_recheck", "strategy_proposal_id": proposal_id,
        })
        args = {
            "userId": user_id, "runId": run_id, "proposalId": proposal_id, "drop": drop,
            "name": "Assigned strategy", "activation": activation, "reason": "Reviewed eligibility",
        }
        mismatches = (
            {"userId": str(uuid4())}, {"name": "Another strategy"},
            {"activation": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
            {"reason": " "}, {"drop": "false"},
        )
        for mismatch in mismatches:
            with pytest.raises(AppError):
                await runtime.data.request("runtimeAutomation:recheck", {**args, **mismatch}, mutation=True)
            run = (await runtime.select("automation_agent_runs", {"id": f"eq.{run_id}"}))[0]
            assert not run.get("outcome")

        # A different proposal cannot be substituted even for the same account and asset.
        other_proposal = str(uuid4())
        await runtime.write("strategy_proposals", {
            "id": other_proposal, "user_id": user_id, "status": "scheduled", "asset": asset,
            "name": "Assigned strategy", "activation_time": activation,
        })
        with pytest.raises(AppError, match="assignment mismatch"):
            await runtime.data.request(
                "runtimeAutomation:recheck", {**args, "proposalId": other_proposal}, mutation=True,
            )

        result = await runtime.data.request("runtimeAutomation:recheck", args, mutation=True)
        expected = "strategy_dropped" if drop else "strategy_reconfirmed"
        assert result["outcome"] == expected
        for retry in (args, {**args, "drop": not drop}):
            repeated = await runtime.data.request("runtimeAutomation:recheck", retry, mutation=True)
            assert repeated["outcome"] == expected
        proposal = (await runtime.select("strategy_proposals", {"id": f"eq.{proposal_id}"}))[0]
        assert proposal["status"] == ("cancelled" if drop else "scheduled")
        other = (await runtime.select("strategy_proposals", {"id": f"eq.{other_proposal}"}))[0]
        assert other["status"] == "scheduled"
        if not shared:
            strategy = (await runtime.select("strategies", {"id": f"eq.{strategy_id}"}))[0]
            assert strategy["status"] == ("cancelled" if drop else "scheduled")


async def test_approval_cannot_reconfirm_a_cancelled_assignment():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        run_id, proposal_id = str(uuid4()), str(uuid4())
        activation = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        await runtime.write("strategy_proposals", {
            "id": proposal_id, "user_id": "global", "status": "cancelled", "name": "Assigned strategy",
            "activation_time": activation,
        })
        await runtime.write("automation_agent_runs", {
            "id": run_id, "user_id": "global", "status": "running", "trigger": "activation_recheck",
            "strategy_proposal_id": proposal_id,
        })
        with pytest.raises(AppError, match="Approval does not match"):
            await runtime.data.request("runtimeAutomation:recheck", {
                "userId": "global", "runId": run_id, "proposalId": proposal_id, "drop": False,
                "name": "Assigned strategy", "activation": activation, "reason": "Setup holds",
            }, mutation=True)
        assert not (await runtime.select("automation_agent_runs", {"id": f"eq.{run_id}"}))[0].get("outcome")


@pytest.mark.asyncio
async def test_account_schedule_creates_strategy_proposal_and_recheck_atomically():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        user_id, saved_id, run_id, snapshot_id = (str(uuid4()) for _ in range(4))
        now = (datetime.now(UTC) + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
        activation = (now + timedelta(minutes=20)).isoformat()
        recheck = (now + timedelta(minutes=13)).isoformat()
        exit_at = (now + timedelta(minutes=30)).isoformat()
        expiry = (now + timedelta(minutes=35)).isoformat()
        template = {
            "name": "Account spread",
            "instrument": {"underlying": "BTC"},
            "enabledForAi": True,
            "entry": {"strategyType": "intraday", "entryAt": now.isoformat(), "exitAt": exit_at},
            "legs": [{"id": "short", "position": "sell", "expiry": session_expiry(now).date().isoformat()}],
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
