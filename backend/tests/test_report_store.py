from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.auth import require_owner
from app.errors import AppError
from app.supabase import SupabaseAdmin


async def test_report_is_persisted_before_job_completion_and_never_sent_to_convex():
    events = []

    async def request(request):
        events.append(request.url.path)
        if request.url.path.endswith("/analysis_reports"):
            return httpx.Response(201, json=[])
        raise AssertionError(request.url)

    settings = SimpleNamespace(
        convex_runtime_enabled=False,
        supabase_url="https://example.test",
        supabase_service_role_key="secret",
    )
    db = SupabaseAdmin(settings)
    await db.client.aclose()
    db.client = httpx.AsyncClient(transport=httpx.MockTransport(request))
    runtime = SimpleNamespace(select=AsyncMock(return_value=[{"id": "run"}]), update=AsyncMock(return_value=[]))

    async def complete(table, payload, params):
        assert events == ["/rest/v1/analysis_reports"]
        assert "report_markdown" not in payload
        assert payload == {"status": "completed"}
        return []

    runtime.update.side_effect = complete
    db.runtime = runtime
    try:
        await db.update(
            "automation_agent_runs",
            {"report_markdown": "Market report", "status": "completed"},
            {"id": "eq.run", "status": "eq.running"},
        )
        runtime.update.assert_awaited_once()
    finally:
        await db.close()


async def test_report_storage_failure_does_not_mark_job_completed():
    settings = SimpleNamespace(
        convex_runtime_enabled=False, supabase_url="https://example.test", supabase_service_role_key="secret"
    )
    db = SupabaseAdmin(settings)
    await db.client.aclose()
    db.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    db.runtime = SimpleNamespace(select=AsyncMock(return_value=[{"id": "run"}]), update=AsyncMock())
    try:
        with pytest.raises(AppError, match="persist"):
            await db.update(
                "automation_agent_runs", {"report_markdown": "Report", "status": "completed"}, {"id": "eq.run"}
            )
        db.runtime.update.assert_not_awaited()
    finally:
        await db.close()


async def test_editable_auth_metadata_cannot_grant_owner_access():
    db = SimpleNamespace(select=AsyncMock(return_value=[{"user_type": "user"}]))
    with pytest.raises(AppError) as caught:
        await require_owner(db, {"id": "user", "user_metadata": {"user_type": "owner"}})
    assert caught.value.status == 403
