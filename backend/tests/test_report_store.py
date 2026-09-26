import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from app.auth import require_owner
from app.database import Database
from app.errors import AppError
from app.report_store import ReportStore

SETTINGS = SimpleNamespace(
    supabase_url="https://example.supabase.test",
    supabase_publishable_key="publishable",
    supabase_service_role_key="service",
    analysis_service_secret="test-analysis-service-secret",
    chart_link_seconds=3600,
    auth_profile_cache_seconds=60,
)
needs_database = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


@needs_database
async def test_report_is_persisted_locally_before_job_completion():
    run_id = str(uuid4())
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        db = Database(SETTINGS, pool)  # type: ignore[arg-type]
        runtime = SimpleNamespace(select=AsyncMock(return_value=[{"id": run_id}]), update=AsyncMock(return_value=[]))

        async def complete(table, payload, params):
            saved = await db.reports.read([run_id], "report_markdown,tool_calls")
            assert saved[run_id]["report_markdown"] == "Market report"
            assert saved[run_id]["tool_calls"] == [{"name": "search"}]
            assert payload == {"status": "completed"}
            return []

        runtime.update.side_effect = complete
        db.runtime = runtime
        try:
            await db.update(
                "automation_agent_runs",
                {"report_markdown": "Market report", "tool_calls": [{"name": "search"}], "status": "completed"},
                {"id": f"eq.{run_id}", "status": "eq.running"},
            )
            runtime.update.assert_awaited_once()
            hydrated = await db.reports.hydrate([{"id": run_id, "status": "completed"}], "id,report_markdown")
            assert hydrated == [{"id": run_id, "status": "completed", "report_markdown": "Market report"}]
        finally:
            await db.close()


@needs_database
async def test_snapshots_are_read_by_snapshot_id_with_their_run():
    run_id, snapshot_id = str(uuid4()), str(uuid4())
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        async with pool.connection() as connection:
            await connection.execute(
                """insert into ai.analysis_reports (id, snapshot_id, market_json)
                   values (%s, %s, '{"chartImages":[{"id":"btc-daily"}],"price":1}')""",
                (run_id, snapshot_id),
            )
        store = ReportStore(pool)
        charts = await store.snapshots({"select": "id,chart_images:market_json->chartImages",
                                        "id": f"in.({snapshot_id})"})
        assert charts == [{"snapshot_id": snapshot_id, "run_id": run_id, "id": snapshot_id,
                           "chart_images": [{"id": "btc-daily"}]}]
        full = await store.snapshots({"id": f"eq.{snapshot_id}"})
        assert full[0]["market_json"]["price"] == 1 and full[0]["created_at"].endswith("+00:00")
        with pytest.raises(AppError):
            await store.snapshots({"user_id": "eq.someone"})


async def test_report_storage_failure_does_not_mark_job_completed():
    db = Database.__new__(Database)
    db.reports = SimpleNamespace(save=AsyncMock(side_effect=AppError(503, "Could not persist", "report_failed")))
    db.runtime = SimpleNamespace(select=AsyncMock(return_value=[{"id": "run"}]), update=AsyncMock())
    with pytest.raises(AppError, match="persist"):
        await db.update("automation_agent_runs", {"report_markdown": "Report", "status": "completed"}, {"id": "eq.run"})
    db.runtime.update.assert_not_awaited()


async def test_unknown_tables_are_rejected_instead_of_reaching_a_remote_database():
    db = Database.__new__(Database)
    with pytest.raises(AppError) as caught:
        await db.select("exchange_connections", {"select": "*"})
    assert caught.value.code == "unsupported_record_query"


async def test_editable_auth_metadata_cannot_grant_owner_access():
    db = SimpleNamespace(profile=AsyncMock(return_value={"user_type": "user"}))
    with pytest.raises(AppError) as caught:
        await require_owner(db, {"id": "user", "user_metadata": {"user_type": "owner"}})
    assert caught.value.status == 403
