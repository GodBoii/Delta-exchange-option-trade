import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.delta import DeltaClient, RequestBudget
from app.delta_events import DeltaEvents
from app.errors import AppError


def test_only_fresh_increasing_mark_events_are_used():
    wake = asyncio.Event()
    feed = DeltaEvents("key", "secret", wake, "wss://unused", "wss://unused")
    feed.connected["public"] = True
    assert feed.mark("CALL", 5) is None
    timestamp = int(time.time() * 1_000_000)
    feed.receive({"type": "mark_price", "sy": "MARK:CALL", "p": "100", "ts": timestamp}, "public")
    assert feed.mark("CALL", 5) == "100"
    feed.receive({"type": "mark_price", "sy": "MARK:CALL", "p": "200", "ts": timestamp - 1}, "public")
    assert feed.mark("CALL", 5) == "100"
    feed.connected["public"] = False
    assert feed.mark("CALL", 5) is None
    assert wake.is_set()


async def test_a_borrowed_client_does_not_close_the_shared_connection():
    released = []
    async with httpx.AsyncClient() as http:
        client = DeltaClient(
            SimpleNamespace(delta_production_url="https://unused"),
            http_client=http,
            release=lambda: released.append(True),
        )
        await client.close()
        await client.close()
        assert released == [True]
        assert not http.is_closed


async def test_rate_limit_reset_blocks_requests_without_sleeping_in_an_exit():
    budget = RequestBudget()
    budget.blocked_until = time.monotonic() + 10
    with pytest.raises(AppError) as error:
        budget.charge(5, True, True)
    assert error.value.code == "delta_rate_limited"


async def test_stale_stream_falls_back_to_rest():
    client = DeltaClient(SimpleNamespace(delta_production_url="https://unused"))
    client.events = SimpleNamespace(mark=lambda *_: None)
    client.ticker = AsyncMock(return_value={"result": {"mark_price": "42"}})
    try:
        assert (await client.risk_ticker("CALL"))["result"]["mark_price"] == "42"
        client.ticker.assert_awaited_once_with("CALL")
    finally:
        await client.close()
