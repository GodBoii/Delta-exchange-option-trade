"""One database listener per API process fans changes out to the right browsers."""

import asyncio
import os
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from app.change_feed import ChangeFeed
from app.local_runtime import LocalRuntimeStore


def test_changes_reach_only_the_owner_and_shared_changes_reach_everyone():
    feed = ChangeFeed("postgresql://unused")
    owner, other = feed.subscribe("owner"), feed.subscribe("other")
    feed.publish("strategies", "owner")
    assert owner.changed.is_set() and not other.changed.is_set()
    assert set(owner.take()) == {"strategies"} and not owner.changed.is_set()
    feed.publish("analysis_jobs", "global")
    assert "automation" in owner.take() and "automation" in other.take()
    feed.publish("unrelated_table", "owner")
    assert not owner.changed.is_set()
    feed.unsubscribe(owner)
    feed.unsubscribe(other)
    assert feed.subscribers == {}


def test_revisions_increase_so_every_change_triggers_a_refetch():
    feed = ChangeFeed("postgresql://unused")
    subscription = feed.subscribe("owner")
    feed.publish("strategies", "owner")
    first = subscription.take()["strategies"]
    feed.publish("strategies", "owner")
    assert subscription.take()["strategies"] > first


@pytest.mark.skipif(not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL")
async def test_committed_writes_are_delivered_through_postgres_notify():
    url = os.environ["TEST_LOCAL_DATABASE_URL"]
    feed = ChangeFeed(url)
    user_id = str(uuid4())
    subscription = feed.subscribe(user_id)
    feed.start()
    try:
        async with asyncio.timeout(10):
            while not feed.connected:
                await asyncio.sleep(0.05)
        async with AsyncConnectionPool(url, open=False) as pool:
            await pool.open()
            await LocalRuntimeStore(pool).write("strategies", {"id": str(uuid4()), "user_id": user_id,
                                                               "status": "scheduled"})
        async with asyncio.timeout(5):
            await subscription.changed.wait()
        assert "strategies" in subscription.take()
    finally:
        await feed.stop()
