"""New shared proposals cannot bypass the writer's expiry and duration checks."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.default_strategies import default_strategy_definitions
from app.errors import AppError
from app.exit_schedule import ExitChoice, resolve_exit_schedule, session_expiry, template_from_definition
from app.local_runtime import LocalRuntimeStore

pytestmark = pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="Requires isolated PostgreSQL")


@pytest.mark.asyncio
async def test_writer_rejects_later_expiry_without_committing_and_accepts_current_session():
    now = datetime.now(UTC)
    entry = now + timedelta(minutes=15)
    expiry = session_expiry(now)
    if expiry - entry < timedelta(minutes=15):
        pytest.skip("Real clock too close to expiry for a future-entry integration fixture")
    source = next(s for s in default_strategy_definitions(now) if s.name == "Short ATM straddle")
    template = template_from_definition(source.model_dump(mode="json"))
    options = [{
        "symbol": f"{kind}-BTC-84000-{expiry:%d%m%y}", "strike": 84000,
        "spot": 84000, "expiry": expiry.isoformat(),
    } for kind in ("C", "P")]
    definition, schedule = resolve_exit_schedule(
        template, entry_at=entry, choice=ExitChoice(kind="intraday", hours=7), options=options, review_at=now,
    )
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        store = LocalRuntimeStore(pool)
        saved_id, run_id, snapshot_id = (str(uuid4()) for _ in range(3))
        async with pool.connection() as db:
            await db.execute(
                """insert into trade.saved_strategies
                   (id,user_id,name,definition_json,enabled_for_ai,version,created_at,updated_at)
                   values (%s,null,%s,%s,true,1,%s,%s)""",
                (saved_id, source.name, Jsonb(template), now, now),
            )
        await store.write("automation_agent_runs", {
            "id": run_id, "user_id": "global", "asset": "BTC", "status": "running", "trigger": "manual",
            "run_key": f"session-policy:{run_id}", "market_snapshot_id": snapshot_id,
            "scheduled_for": now.isoformat(),
        })
        args = {
            "runId": run_id, "candidates": [{"id": saved_id, "version": 1}], "snapshotId": snapshot_id,
            "activation": entry.isoformat(), "expiry": (entry + timedelta(minutes=15)).isoformat(),
            "exit": schedule["exitUtc"], "confidence": 0.7, "reasoning": "Current session evidence",
            "supporting": [], "invalidation": [],
        }
        invalid = json.loads(json.dumps(definition))
        for leg in invalid["legs"]:
            leg["expiry"] = (expiry + timedelta(days=1)).date().isoformat()
        with pytest.raises(AppError) as raised:
            await store.data.request(
                "sharedAnalysis:publish", {**args, "definitionJson": json.dumps(invalid)}, mutation=True
            )
        assert raised.value.code == "session_schedule_invalid"
        run = (await store.select("automation_agent_runs", {"id": f"eq.{run_id}"}))[0]
        assert not run.get("outcome")
        result = await store.data.request(
            "sharedAnalysis:publish", {**args, "definitionJson": json.dumps(definition)}, mutation=True
        )
        assert result["outcome"] == "strategy_selected"
