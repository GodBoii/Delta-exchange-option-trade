from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class FixedSession:
    trigger: str
    zone: ZoneInfo
    local_time: time


@dataclass(frozen=True, slots=True)
class FixedRun:
    trigger: str
    scheduled_for: datetime
    run_key: str


FIXED_SESSIONS = (
    FixedSession("asia_session", ZoneInfo("Asia/Tokyo"), time(9, 0)),
    FixedSession("london_session", ZoneInfo("Europe/London"), time(8, 0)),
    FixedSession("new_york_session", ZoneInfo("America/New_York"), time(9, 30)),
)


def parse_aware_datetime(value: str, field: str, *, now: datetime | None = None) -> datetime:
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(
            f"{field} must be ISO-8601 UTC (2026-08-30T00:00:00Z) "
            "or IST (2026-08-30T05:30:00+05:30)"
        ) from error
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include Z, +00:00, or +05:30")
    parsed = parsed.astimezone(UTC)
    if parsed <= (now or datetime.now(UTC)):
        raise ValueError(f"{field} must be in the future")
    return parsed


def normalize_run_time(value: str, field: str, *, now: datetime | None = None) -> datetime:
    parsed = parse_aware_datetime(value, field, now=now)
    if parsed.second or parsed.microsecond:
        parsed += timedelta(minutes=1)
    return parsed.replace(second=0, microsecond=0)


def fixed_runs_between(start: datetime, end: datetime) -> list[FixedRun]:
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    runs: list[FixedRun] = []
    for session in FIXED_SESSIONS:
        local_start = start.astimezone(session.zone).date() - timedelta(days=1)
        local_end = end.astimezone(session.zone).date() + timedelta(days=1)
        current = local_start
        while current <= local_end:
            scheduled = datetime.combine(current, session.local_time, session.zone).astimezone(UTC)
            if start <= scheduled <= end:
                runs.append(
                    FixedRun(
                        trigger=session.trigger,
                        scheduled_for=scheduled,
                        run_key=f"{session.trigger}:{current.isoformat()}",
                    )
                )
            current += timedelta(days=1)
    return sorted(runs, key=lambda run: run.scheduled_for)


def next_fixed_run(after: datetime) -> FixedRun:
    after = after.astimezone(UTC)
    runs = fixed_runs_between(after + timedelta(microseconds=1), after + timedelta(days=2))
    if not runs:
        raise RuntimeError("No fixed automation session could be calculated")
    return runs[0]


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def ist_text(value: datetime) -> str:
    return value.astimezone(IST).isoformat()


def ist_day_bounds(value: datetime) -> tuple[datetime, datetime]:
    local_day: date = value.astimezone(IST).date()
    start = datetime.combine(local_day, time.min, IST).astimezone(UTC)
    return start, start + timedelta(days=1)
