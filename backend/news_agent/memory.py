"""Bounded public event memory, shared by BTC and ETH without conversation history."""

import json
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb


def recent_events(database_url: str, asset: str, now: datetime) -> list[dict]:
    with psycopg.connect(database_url) as connection:
        connection.execute("set transaction read only")
        rows = connection.execute(
            "select payload || jsonb_build_object('first_seen_at',first_seen_at,'last_seen_at',updated_at) "
            "from ai.news_events where updated_at >= %s "
            "and (payload->'assets' ? %s or payload->'assets' ? 'MACRO') "
            "order by updated_at desc limit 20",
            (now - timedelta(days=7), asset),
        ).fetchall()
    return [row[0] for row in rows]


def save_events(database_url: str, events: list[dict]) -> None:
    if not events:
        return
    with psycopg.connect(database_url) as connection:
        for event in events:
            if not event.get("metadata_verified"):
                continue
            if any(claim.get("source_kind") == "discussion" for claim in event.get("claims", [])):
                continue
            # Do not revive retracted/corrected events from an older observation of the same document.
            connection.execute(
                "insert into ai.news_events(event_id,payload) values (%s,%s) "
                "on conflict(event_id) do update set payload=excluded.payload,updated_at=now() "
                "where ai.news_events.payload->>'status' not in ('retracted','corrected') "
                "or excluded.payload->>'status' in ('retracted','corrected')",
                (event["event_id"], Jsonb(event)),
            )
        connection.execute(
            "delete from ai.news_events where updated_at < %s", (datetime.now(UTC) - timedelta(days=30),)
        )


def memory_context(events: list[dict]) -> str:
    if not events:
        return ""
    compact = [
        {
            key: event.get(key)
            for key in (
                "event_id",
                "title",
                "status",
                "assets",
                "source_urls",
                "event_at",
                "first_seen_at",
                "last_seen_at",
            )
        }
        for event in events
    ]
    return "\nHistorical event memory, not current evidence. Recheck material updates and corrections:\n" + json.dumps(
        compact
    )
