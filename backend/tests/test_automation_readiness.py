import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import automation
from app.errors import AppError

# Per-account analysis; the shared-analysis path has its own tests.
PRIVATE_ANALYSIS = SimpleNamespace(shared_analysis_enabled=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["healthy", "starting", "wrong_service", "invalid_json", "http_error", "offline"])
async def test_readiness_requires_a_healthy_analysis_service(monkeypatch, case) -> None:
    async def health(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.method == "GET"
        if case == "offline":
            raise httpx.ConnectError("Connection refused", request=request)
        if case == "invalid_json":
            return httpx.Response(200, text="Starting")
        return httpx.Response(
            503 if case == "http_error" else 200,
            json={
                "success": True,
                "service": "other-service" if case == "wrong_service" else "news-analyzer",
                "databaseReady": case != "starting",
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        automation.httpx, "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(health), **kwargs),
    )
    if case == "healthy":
        await automation.require_analyzer_ready()
    else:
        with pytest.raises(AppError) as caught:
            await automation.require_analyzer_ready()
        assert caught.value.status == 503
        assert caught.value.code == "automation_service_unavailable"


@pytest.mark.asyncio
async def test_scheduled_run_waits_unclaimed_until_service_recovers(monkeypatch) -> None:
    row = {
        "id": "run-1", "user_id": "user-1", "trigger": "new_york_session", "status": "scheduled",
        "scheduled_for": datetime.now(UTC).isoformat(),
    }
    claims = []

    class Database:

        settings = PRIVATE_ANALYSIS
        async def update(self, *_args):
            return []

        async def select(self, table, _params):
            if table == "automation_settings":
                return [{"user_id": "user-1"}]
            return [row] if _params.get("trigger") == "neq.activation_recheck" and row["status"] == "scheduled" else []

        async def rpc(self, _name, payload):
            claims.append(payload)
            row["status"] = "running"
            return [row]

    ready = AsyncMock(side_effect=[AppError(503, "Starting", "automation_service_unavailable"), None])
    monkeypatch.setattr(automation, "require_analyzer_ready", ready)
    scheduler = automation.AutomationScheduler(Database(), object())
    execute = AsyncMock()
    monkeypatch.setattr(scheduler, "_execute", execute)

    await scheduler._process_due_runs()
    assert row["status"] == "scheduled"
    assert claims == []
    execute.assert_not_awaited()

    await scheduler._process_due_runs()
    await asyncio.gather(*scheduler.running_tasks)
    assert len(claims) == 1
    execute.assert_awaited_once_with(row)


@pytest.mark.asyncio
async def test_manual_run_does_not_create_a_failed_run_while_service_starts(monkeypatch) -> None:
    db = SimpleNamespace(
        insert=AsyncMock(), profile=AsyncMock(return_value={"user_type": "owner"}), settings=PRIVATE_ANALYSIS
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, engine=object())))
    monkeypatch.setattr(automation, "current_account", AsyncMock())
    monkeypatch.setattr(automation, "ensure_settings", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(
        automation, "require_analyzer_ready",
        AsyncMock(side_effect=AppError(503, "Starting", "automation_service_unavailable")),
    )

    with pytest.raises(AppError) as caught:
        await automation.run_automation(request, automation.AutomationRunRequest(), {"id": "user-1"})

    assert caught.value.status == 503
    db.insert.assert_not_awaited()
