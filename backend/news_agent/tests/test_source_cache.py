import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from news_agent.source_cache import FileSourceCache, SourceCache, SourceSnapshot


def test_cache_shares_one_fetch_between_different_event_loops():
    cache = SourceCache()
    calls = []

    async def load():
        calls.append(1)
        await asyncio.sleep(0.05)
        return SourceSnapshot([{"title": "CPI"}], "2026-10-12T00:00:00Z")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(asyncio.run, cache.get(("calendar",), load))
        second = executor.submit(asyncio.run, cache.get(("calendar",), load))
        assert first.result().cards == second.result().cards
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_file_cache_survives_new_worker_and_does_not_mutate_shared_cards(tmp_path):
    calls = []

    async def load():
        calls.append(1)
        return SourceSnapshot([{"title": "CPI"}], "2026-10-12T00:00:00Z")

    first = await FileSourceCache(tmp_path).get(("calendar",), load)
    first.cards[0]["title"] = "mutated"
    second = await FileSourceCache(tmp_path).get(("calendar",), load)
    assert second.cards[0]["title"] == "CPI"
    assert second.last_success_at == "2026-10-12T00:00:00Z"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failure_backoff_expires_and_preserves_previous_success():
    now = [0.0]
    cache = SourceCache(clock=lambda: now[0])
    calls = []

    async def good():
        return SourceSnapshot([], "first", retry_seconds=1)

    async def blocked():
        calls.append(1)
        return SourceSnapshot([], "second", "403", "blocked", 900)

    await cache.get(("source",), good)
    now[0] = 2
    result = await cache.get(("source",), blocked)
    assert result.last_success_at == "first"
    now[0] = 100
    assert (await cache.get(("source",), blocked)).error_code == "blocked"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_file_cache_instances_fetch_only_once(tmp_path):
    calls = []

    async def load():
        calls.append(1)
        await asyncio.sleep(0.05)
        return SourceSnapshot([], "now")

    await asyncio.gather(FileSourceCache(tmp_path).get(("feed",), load), FileSourceCache(tmp_path).get(("feed",), load))
    assert len(calls) == 1
