"""The writer independently validates short holds using its stored review context."""

from datetime import UTC, datetime, timedelta

import pytest

from app.errors import AppError
from app.exit_schedule import session_expiry
from app.local_research_operations import LocalResearchOperations
from automation_agent.team import session_instructions


def definition(entry: datetime, hours: int) -> dict:
    return {
        "entry": {"entryAt": entry.isoformat(), "exitAt": (entry + timedelta(hours=hours)).isoformat()},
        "legs": [{"expiry": session_expiry(entry).date().isoformat()}],
        "exitMinutesBeforeExpiry": 5,
    }


@pytest.mark.parametrize("trigger", ["asia_session", "manual", "agent_follow_up", "midnight_review"])
def test_writer_rejects_eleven_hours_without_evening_job(trigger):
    entry = datetime(2026, 10, 8, 13, 45, tzinfo=UTC)
    run = {"data": {"trigger": trigger, "scheduled_for": (entry - timedelta(minutes=15)).isoformat()}}
    with pytest.raises(AppError, match="maximum"):
        LocalResearchOperations._session_policy(definition(entry, 11), run)


def test_writer_evening_exception_is_bounded_and_expiry_cannot_be_forged():
    entry = datetime(2026, 10, 8, 13, 45, tzinfo=UTC)
    run = {"data": {"trigger": "new_york_session", "scheduled_for": (entry - timedelta(minutes=15)).isoformat()}}
    valid = definition(entry, 11)
    LocalResearchOperations._session_policy(valid, run)
    valid["legs"][0]["expiry"] = "2026-10-10"
    with pytest.raises(AppError, match="upcoming"):
        LocalResearchOperations._session_policy(valid, run)
    with pytest.raises(AppError, match="maximum"):
        LocalResearchOperations._session_policy(definition(entry, 12), run)


def test_writer_rejects_deferral_to_another_session():
    entry = datetime(2026, 10, 8, 13, tzinfo=UTC)
    run = {"data": {"trigger": "pre_expiry", "scheduled_for": "2026-10-08T10:00:00Z"}}
    with pytest.raises(AppError, match="session"):
        LocalResearchOperations._session_policy(definition(entry, 7), run)


@pytest.mark.parametrize("trigger,hours", [
    ("asia_session", 7), ("london_session", 7), ("pre_expiry", 7),
    ("new_york_session", 11), ("midnight_review", 7),
])
def test_session_instructions_state_remaining_window_and_limit(trigger, hours):
    instructions = session_instructions(trigger, datetime(2026, 10, 8, 10, tzinfo=UTC))
    assert "120.0 minutes remaining" in instructions
    assert f"Maximum hold is {hours} hours" in instructions
    if trigger == "pre_expiry":
        assert "do not move to tomorrow" in instructions


@pytest.mark.parametrize("reference,expected", [
    ("2026-10-08T11:59:59Z", "2026-10-08T12:00:00+00:00"),
    ("2026-10-08T12:00:00Z", "2026-10-09T12:00:00+00:00"),
    ("2026-10-08T13:30:00Z", "2026-10-09T12:00:00+00:00"),
])
def test_expiry_boundary_rollover(reference, expected):
    assert session_expiry(datetime.fromisoformat(reference)).isoformat() == expected
