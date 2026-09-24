import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from starlette.requests import Request


def app_module(monkeypatch):
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test")
    return importlib.import_module("app.main")


async def test_read_replica_rejects_all_write_methods(monkeypatch):
    main = app_module(monkeypatch)
    monkeypatch.setattr(main, "settings", SimpleNamespace(trading_writer_enabled=False))
    call_next = AsyncMock()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        request = Request({"type": "http", "method": method, "path": "/api/automation/run"})
        response = await main.require_trading_writer(request, call_next)
        assert response.status_code == 503
    call_next.assert_not_awaited()


async def test_read_replica_serves_reads_and_writer_still_accepts_changes(monkeypatch):
    main = app_module(monkeypatch)
    response = object()
    call_next = AsyncMock(return_value=response)
    monkeypatch.setattr(main, "settings", SimpleNamespace(trading_writer_enabled=False))
    request = Request({"type": "http", "method": "GET", "path": "/api/session"})
    assert await main.require_trading_writer(request, call_next) is response
    monkeypatch.setattr(main, "settings", SimpleNamespace(trading_writer_enabled=True))
    request = Request({"type": "http", "method": "POST", "path": "/api/automation/run"})
    assert await main.require_trading_writer(request, call_next) is response


async def test_read_replica_lifespan_starts_no_writer_jobs_or_private_streams(monkeypatch):
    main = app_module(monkeypatch)
    from app.config import Settings

    settings = Settings(
        trading_writer_enabled=False, scheduler_enabled=False,
        automation_scheduler_enabled=False, delta_events_enabled=True,
    )
    monkeypatch.setattr(main, "settings", settings)
    async with main.lifespan(main.app):
        assert not main.app.state.scheduler.enabled
        assert main.app.state.scheduler.task is None
        assert main.app.state.automation_scheduler.task is None
        assert not main.app.state.engine.settings.delta_events_enabled
