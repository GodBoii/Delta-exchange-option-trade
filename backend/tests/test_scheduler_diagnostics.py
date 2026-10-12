import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import automation
from app.retained_logs import RedactedFormatter, configure_retained_logs


async def test_expired_review_preserves_last_admission_blocker(monkeypatch):
    row = {"id": "run", "user_id": "account", "asset": "BTC", "trigger": "london_session",
           "status": "scheduled", "scheduled_for": (datetime.now(UTC) - timedelta(minutes=11)).isoformat()}

    async def update(_table, values, _params):
        row.update(values)
        return [row]

    async def select(table, params):
        if table == "automation_settings":
            return [{"user_id": "account"}]
        return [row.copy()] if params["trigger"] == "neq.activation_recheck" else []

    db = SimpleNamespace(settings=SimpleNamespace(shared_analysis_enabled=False), update=update, select=select)
    await automation.defer_automation_run(db, "account", "run", status="scheduled",
                                         message=automation.UNKNOWN_CAPACITY_MESSAGE)
    deferred_at = row["last_deferred_at"]
    scheduler = automation.AutomationScheduler(db, object())
    capacity = AsyncMock()
    monkeypatch.setattr(automation, "has_automation_trade_capacity", capacity)
    await scheduler._process_due_runs()
    assert row["status"] == "cancelled"
    assert row["expiry_reason"] == "capital_capacity_unavailable"
    assert row["last_deferred_at"] == deferred_at
    capacity.assert_not_awaited()


async def test_scheduler_health_detects_stalled_and_stopped_loop(monkeypatch):
    scheduler = automation.AutomationScheduler(SimpleNamespace(settings=SimpleNamespace()), object())
    scheduler.task = asyncio.create_task(asyncio.sleep(60))
    try:
        monkeypatch.setattr(automation.time, "monotonic", lambda: 1000)
        assert not scheduler.status()["healthy"]
        scheduler.last_completed_monotonic = 999
        assert scheduler.status()["healthy"]
        scheduler.last_error = "Automation scheduler polling cycle failed"
        assert not scheduler.status()["healthy"]
        scheduler.last_error = None
        scheduler.last_completed_monotonic = 800
        assert not scheduler.status()["healthy"]
    finally:
        scheduler.task.cancel()
        await asyncio.gather(scheduler.task, return_exceptions=True)
    assert not scheduler.status()["running"]


async def test_restart_backfills_review_seven_minutes_late(monkeypatch):
    now = datetime(2026, 10, 12, 7, 7, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(automation, "datetime", Clock)
    db = SimpleNamespace(settings=SimpleNamespace(shared_analysis_enabled=True, automation_assets="BTC,ETH"),
                         select=AsyncMock(return_value=[{"user_id": "global", "enabled": True}]), rpc=AsyncMock())
    await automation.AutomationScheduler(db, object())._enqueue_session_reviews()
    payload = db.rpc.await_args_list[0].args[1]["p_runs"]
    assert {row["asset"] for row in payload if row["scheduled_for"] == "2026-10-12T07:00:00Z"} == {"BTC", "ETH"}


def test_backend_logs_survive_reconfiguration_and_redact_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOCAL_DATABASE_URL", "private-database-url")
    monkeypatch.setenv("ANALYSIS_SERVICE_SECRET", "private-service-secret")
    root = logging.getLogger()
    before = root.handlers.copy()
    try:
        configure_retained_logs()
        configure_retained_logs()
        added = [handler for handler in root.handlers if handler not in before]
        assert len(added) == 1
        record = logging.LogRecord("audit", logging.ERROR, "", 0,
                                   "capacity private-database-url private-service-secret", (), None)
        added[0].handle(record)
        added[0].flush()
        contents = next(tmp_path.glob("backend-*.log")).read_text()
        assert "capacity [redacted] [redacted]" in contents
        assert "private-database-url" not in contents
        assert isinstance(added[0].formatter, RedactedFormatter)
    finally:
        for handler in root.handlers.copy():
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
