import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app import automation
from app.automation import AutomationScheduler
from app.automation_schedule import (
    fixed_runs_between,
    ist_day_bounds,
    next_fixed_run,
    normalize_run_time,
    parse_aware_datetime,
    utc_text,
)


def test_utc_and_ist_inputs_resolve_to_the_same_instant() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)

    utc_value = parse_aware_datetime("2026-08-30T00:00:00Z", "next_run_time", now=now)
    ist_value = parse_aware_datetime("2026-08-30T05:30:00+05:30", "next_run_time", now=now)

    assert utc_value == ist_value == datetime(2026, 8, 30, tzinfo=UTC)
    assert utc_text(ist_value) == "2026-08-30T00:00:00Z"


def test_run_time_rounds_up_to_one_utc_minute() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)

    normalized = normalize_run_time("2026-08-29T05:30:00.001+05:30", "next_run_time", now=now)

    assert normalized == datetime(2026, 8, 29, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    ["2026-08-30T05:30:00", "2026-08-30 05:30 IST", "not-a-time"],
)
def test_timestamp_requires_an_unambiguous_offset(value: str) -> None:
    with pytest.raises(ValueError):
        parse_aware_datetime(value, "next_run_time", now=datetime(2026, 8, 29, tzinfo=UTC))


def test_timestamp_rejects_present_and_past_values() -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)

    with pytest.raises(ValueError, match="future"):
        parse_aware_datetime("2026-08-29T00:00:00Z", "next_run_time", now=now)


def test_fixed_sessions_follow_summer_timezones() -> None:
    start = datetime(2026, 8, 29, tzinfo=UTC)
    runs = fixed_runs_between(start, start + timedelta(days=1))

    assert [(run.trigger, run.scheduled_for) for run in runs] == [
        ("asia_session", datetime(2026, 8, 29, 0, 0, tzinfo=UTC)),
        ("london_session", datetime(2026, 8, 29, 7, 0, tzinfo=UTC)),
        ("new_york_session", datetime(2026, 8, 29, 13, 30, tzinfo=UTC)),
        ("asia_session", datetime(2026, 8, 30, 0, 0, tzinfo=UTC)),
    ]


def test_fixed_sessions_follow_winter_timezones() -> None:
    start = datetime(2026, 1, 15, tzinfo=UTC)
    runs = fixed_runs_between(start, start + timedelta(hours=23, minutes=59))

    assert [(run.trigger, run.scheduled_for.hour, run.scheduled_for.minute) for run in runs] == [
        ("asia_session", 0, 0),
        ("london_session", 8, 0),
        ("new_york_session", 14, 30),
    ]


def test_next_fixed_run_excludes_the_current_instant() -> None:
    current_asia_run = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)

    fixed = next_fixed_run(current_asia_run)

    assert fixed.trigger == "london_session"
    assert fixed.scheduled_for == datetime(2026, 8, 29, 7, 0, tzinfo=UTC)


def test_daily_limit_uses_ist_calendar_boundaries() -> None:
    start, end = ist_day_bounds(datetime(2026, 8, 29, 20, 0, tzinfo=UTC))

    assert start == datetime(2026, 8, 29, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 8, 30, 18, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_scheduler_precreates_fixed_reviews_in_one_database_call() -> None:
    class Database:
        payload: list[dict] = []
        reconciled = False

        async def select(self, table: str, _params: dict) -> list[dict]:
            assert table == "automation_settings"
            return [{"user_id": "user-1"}]

        async def rpc(self, function: str, payload: dict) -> int:
            if function == "ensure_automation_fixed_runs":
                self.payload = payload["p_runs"]
            elif function == "cancel_redundant_automation_followups":
                self.reconciled = True
            else:
                raise AssertionError(f"Unexpected RPC: {function}")
            return 0

    database = Database()
    scheduler = AutomationScheduler(database, object())  # type: ignore[arg-type]

    await scheduler._enqueue_session_reviews()

    assert {row["trigger"] for row in database.payload} == {
        "asia_session",
        "london_session",
        "new_york_session",
    }
    assert all(str(row["scheduled_for"]).endswith("Z") for row in database.payload)
    assert database.reconciled


@pytest.mark.asyncio
async def test_scheduler_claims_due_run_and_passes_follow_up_signals() -> None:
    due = {
        "id": "run-1",
        "user_id": "user-1",
        "trigger": "agent_follow_up",
        "reason": "Check the breakout",
        "signals_to_inspect": ["volume", "price"],
        "scheduled_for": utc_text(datetime.now(UTC)),
    }

    class Database:
        async def select(self, table: str, _params: dict) -> list[dict]:
            if table == "automation_settings":
                return [{"user_id": "user-1"}]
            return [due]

        async def update(self, _table: str, _payload: dict, _params: dict) -> list[dict]:
            return []

        async def rpc(self, function: str, _payload: dict) -> list[dict]:
            assert function == "claim_automation_agent_run"
            return [due]

    received: list[dict] = []
    scheduler = AutomationScheduler(Database(), object())  # type: ignore[arg-type]

    async def execute(row: dict) -> None:
        received.append(row)

    scheduler._execute = execute  # type: ignore[method-assign]
    await scheduler._process_due_runs()
    if scheduler.running_tasks:
        await asyncio.gather(*scheduler.running_tasks)

    assert received[0]["signals_to_inspect"] == ["volume", "price"]


@pytest.mark.asyncio
async def test_scheduler_cancels_stale_run_without_executing_it() -> None:
    due = {
        "id": "run-1",
        "user_id": "user-1",
        "trigger": "asia_session",
        "reason": "Fixed asia session review",
        "signals_to_inspect": [],
        "scheduled_for": utc_text(datetime.now(UTC) - timedelta(minutes=11)),
    }

    class Database:
        updates: list[dict] = []

        async def select(self, table: str, _params: dict) -> list[dict]:
            if table == "automation_settings":
                return [{"user_id": "user-1"}]
            return [due]

        async def update(self, _table: str, payload: dict, _params: dict) -> list[dict]:
            self.updates.append(payload)
            return []

        async def rpc(self, _function: str, _payload: dict) -> list[dict]:
            raise AssertionError("A stale run must not be claimed")

    database = Database()
    scheduler = AutomationScheduler(database, object())  # type: ignore[arg-type]

    await scheduler._process_due_runs()

    assert any(update.get("status") == "cancelled" for update in database.updates)
    assert not scheduler.running_tasks


@pytest.mark.asyncio
async def test_committed_outcome_survives_provider_timeout(monkeypatch) -> None:
    class Database:
        updates: list[dict] = []

        async def select(self, _table: str, _params: dict) -> list[dict]:
            return [{"outcome": "wait_and_run_again", "market_snapshot_id": "snapshot-1"}]

        async def update(self, _table: str, payload: dict, _params: dict) -> list[dict]:
            self.updates.append(payload)
            return [payload]

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            raise TimeoutError("provider timeout")

    async def account_context(_engine, _user_id: str) -> dict:
        return {}

    database = Database()
    monkeypatch.setattr(automation, "build_account_context", account_context)
    monkeypatch.setattr(automation.httpx, "AsyncClient", lambda **_kwargs: FailingClient())

    payload = await automation.execute_automation_run(
        db=database,  # type: ignore[arg-type]
        engine=object(),  # type: ignore[arg-type]
        user_id="user-1",
        run_id="run-1",
        session_id="session-1",
        trigger="asia_session",
        reason="Fixed review",
    )

    assert payload["outcome"] == "wait_and_run_again"
    assert any(update.get("status") == "completed" for update in database.updates)
