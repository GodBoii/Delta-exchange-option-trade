from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import automation
from app.errors import AppError


class RunDatabase:
    def __init__(self, started_at: datetime) -> None:
        self.started_at = started_at
        self.row = {"status": "running", "outcome": None}

    async def select(self, table: str, _params: dict) -> list[dict]:
        return [self.row] if table == "automation_agent_runs" else []

    async def update(self, _table: str, payload: dict, params: dict) -> list[dict]:
        if self.row["status"] != params["status"].removeprefix("eq."):
            return []
        if "started_at" in params:
            cutoff = datetime.fromisoformat(params["started_at"].removeprefix("lt.").replace("Z", "+00:00"))
            if self.started_at >= cutoff:
                return []
        self.row.update(payload)
        return [self.row]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger,elapsed_seconds", [("london_session", 1221), ("activation_recheck", 311)])
async def test_long_review_completes_but_recheck_keeps_its_deadline(monkeypatch, trigger, elapsed_seconds) -> None:
    started_at = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)
    database = RunDatabase(started_at)
    scheduler = automation.AutomationScheduler(database, object())

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (started_at + timedelta(seconds=elapsed_seconds)).astimezone(tz)

    async def context(*_args):
        return {}

    async def respond(request: httpx.Request) -> httpx.Response:
        await scheduler._process_due_runs()
        assert database.row["status"] == "running"
        if elapsed_seconds > request.extensions["timeout"]["read"]:
            raise httpx.ReadTimeout("", request=request)
        return httpx.Response(200, json={"outcome": "no_trade_for_current_window", "report": "No trade"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(automation, "datetime", Clock)
    monkeypatch.setattr(automation, "build_account_context", context)
    monkeypatch.setattr(automation, "build_activation_recheck_context", context)
    monkeypatch.setattr(
        automation.httpx, "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    kwargs = dict(
        db=database, engine=object(), user_id="user-1", run_id="run-1", session_id="session-1",
        trigger=trigger, reason="Fixed review", strategy_proposal_id="proposal-1",
    )
    if trigger == "activation_recheck":
        with pytest.raises(AppError, match="within 310 seconds"):
            await automation.execute_automation_run(**kwargs)
        assert database.row["status"] == "failed"
        assert database.row["error"] == "Automation analysis did not respond within 310 seconds"
    else:
        await automation.execute_automation_run(**kwargs)
        assert database.row["status"] == "completed"
        assert database.row["report_markdown"].startswith(
            "## Verified action\n\nNo strategy was scheduled during this review."
        )
        assert "No trade" in database.row["report_markdown"]
        assert "## Decision\n\nNo strategy was scheduled during this review." in database.row["report_markdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("age_minutes,expected", [(21, "running"), (49, "running"), (51, "failed")])
async def test_scheduler_only_expires_runs_after_analysis_and_persistence_window(age_minutes, expected) -> None:
    database = RunDatabase(datetime.now(UTC) - timedelta(minutes=age_minutes))
    scheduler = automation.AutomationScheduler(database, object())

    await scheduler._process_due_runs()

    assert database.row["status"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("error_type", [httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError])
async def test_interrupted_analysis_is_not_resubmitted_and_preserves_committed_action(
    monkeypatch, committed, error_type,
) -> None:
    database = RunDatabase(datetime.now(UTC))
    requests = []

    async def context(*_args):
        return {}

    async def disconnect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if committed:
            database.row["outcome"] = "strategy_selected"
        raise error_type("Server disconnected without sending a response", request=request)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(automation, "build_account_context", context)
    monkeypatch.setattr(
        automation.httpx, "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(disconnect), **kwargs),
    )
    kwargs = dict(
        db=database, engine=object(), user_id="user-1", run_id="run-1", session_id="session-1",
        trigger="new_york_session", reason="Fixed review",
    )
    if committed:
        result = await automation.execute_automation_run(**kwargs)
        assert result["outcome"] == "strategy_selected"
        assert database.row["status"] == "completed"
        assert database.row["error"] == "Final report unavailable"
    else:
        with pytest.raises(AppError) as caught:
            await automation.execute_automation_run(**kwargs)
        assert caught.value.code == "automation_analysis_interrupted"
        assert database.row["status"] == "failed"
        assert "disconnected" in database.row["error"]
        assert "not retried" in database.row["error"]
    assert len(requests) == 1
