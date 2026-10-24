"""Describe publication age without turning an ambiguous timestamp into a current event."""

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any


def publication_metadata(value: Any, now: datetime | None = None, lookback_hours: int = 48) -> dict:
    now = now or datetime.now(UTC)
    raw = value.isoformat() if isinstance(value, datetime) else str(value or "").strip()[:200]
    moment = None
    if raw:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            with suppress(ValueError, TypeError, OverflowError):
                moment = parsedate_to_datetime(raw)
    if moment is None or moment.utcoffset() is None:
        return {
            "published_at": None,
            "reported_published_at": raw or None,
            "publication_status": "undated",
            "age_hours": None,
        }
    moment = moment.astimezone(UTC)
    age = now - moment
    status = (
        "future_date" if age < -timedelta(minutes=5) else "stale" if age > timedelta(hours=lookback_hours) else "recent"
    )
    return {
        "published_at": moment.isoformat(),
        "reported_published_at": raw,
        "publication_status": status,
        "age_hours": round(age.total_seconds() / 3600, 3),
    }
