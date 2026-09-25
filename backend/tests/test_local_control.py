"""Check shared decision allocation against real PostgreSQL constraints."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

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
async def test_shared_allocation_is_idempotent_and_stops_rescanning():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        runtime = LocalRuntimeStore(pool)
        user_id, saved_id, decision_id, recheck_id = (str(uuid4()) for _ in range(4))
        now = datetime.now(UTC)
        activation = (now + timedelta(minutes=20)).isoformat()
        expiry = (now + timedelta(minutes=35)).isoformat()
        exit_time = (now + timedelta(minutes=30)).isoformat()
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
                (saved_id, "Test spread", Jsonb({"name": "Test spread"}), now, now),
            )
        await runtime.write(
            "strategy_proposals",
            {
                "id": decision_id,
                "user_id": "global",
                "status": "scheduled",
                "agent_run_id": str(uuid4()),
                "saved_strategy_id": saved_id,
                "saved_strategy_version": 1,
                "candidates": [{"id": saved_id, "version": 1}],
                "name": "Test spread",
                "definition_json": {"name": "Test spread"},
                "activation_time": activation,
                "proposal_expiry": expiry,
                "exit_at": exit_time,
                "ai_confidence": 0.9,
                "reasoning_summary": "Test",
                "supporting_signals": [],
                "invalidation_signals": [],
                "market_snapshot_id": str(uuid4()),
                "shared_recheck_run_id": recheck_id,
            },
        )
        await runtime.write(
            "automation_agent_runs",
            {
                "id": recheck_id,
                "user_id": "global",
                "status": "completed",
                "trigger": "activation_recheck",
                "strategy_proposal_id": decision_id,
                "scheduled_for": now.isoformat(),
                "outcome": "strategy_reconfirmed",
            },
        )
        page = await runtime.data.request("sharedAnalysis:pendingAllocationPage", {"cursor": None})
        assert {"decisionId": decision_id, "userId": user_id} in page["items"]
        first = await runtime.data.request(
            "sharedAnalysis:allocate",
            {
                "decisionId": decision_id,
                "userId": user_id,
            },
            mutation=True,
        )
        second = await runtime.data.request(
            "sharedAnalysis:allocate",
            {
                "decisionId": decision_id,
                "userId": user_id,
            },
            mutation=True,
        )
        assert first["reused"] is False
        assert second == {"strategyId": first["strategyId"], "reused": True}
        await runtime.data.request("sharedAnalysis:completeAllocation", {"decisionId": decision_id}, mutation=True)
        assert (await runtime.data.request("sharedAnalysis:pendingAllocationPage", {"cursor": None}))["items"] == []
