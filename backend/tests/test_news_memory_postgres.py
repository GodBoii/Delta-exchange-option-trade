import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from news_agent.memory import recent_events, save_events

pytestmark = pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="Requires isolated PostgreSQL")


def test_public_event_memory_shares_macro_and_preserves_retraction():
    url = os.environ["TEST_LOCAL_DATABASE_URL"]
    identifier = str(uuid4())
    record = {
        "event_id": identifier,
        "metadata_verified": True,
        "assets": ["MACRO"],
        "status": "reported",
        "title": "A public statement",
        "claims": [],
        "source_urls": [],
    }
    try:
        save_events(url, [record])
        assert any(item["event_id"] == identifier for item in recent_events(url, "BTC", datetime.now(UTC)))
        assert any(item["event_id"] == identifier for item in recent_events(url, "ETH", datetime.now(UTC)))
        save_events(url, [{**record, "status": "retracted"}])
        save_events(url, [record])
        with psycopg.connect(url) as connection:
            row = connection.execute("select payload from ai.news_events where event_id=%s", (identifier,)).fetchone()
            assert row[0]["status"] == "retracted"
        save_events(url, [{**record, "event_id": identifier + "bad", "metadata_verified": False}])
        with psycopg.connect(url) as connection:
            assert not connection.execute(
                "select 1 from ai.news_events where event_id=%s", (identifier + "bad",)
            ).fetchone()
    finally:
        with psycopg.connect(url) as connection:
            connection.execute("delete from ai.news_events where event_id=any(%s)", ([identifier, identifier + "bad"],))
