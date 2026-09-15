import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app.client import BinanceMarketClient
from app.config import Settings
from app.main import btcusd_candles


async def test_history_does_not_append_a_current_out_of_range_candle():
    rows = [{"openTime": 1}, {"openTime": 2}]
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        market=SimpleNamespace(candles=AsyncMock(return_value=rows)),
        feed=SimpleNamespace(snapshot=lambda: {"candles": {"1h": {"openTime": 1000}}, "realtime": {}}),
    )))
    await btcusd_candles(request, "1h", 2, 1, 2)
    assert rows == [{"openTime": 1}, {"openTime": 2}]


async def test_identical_requests_share_work_but_different_requests_can_progress():
    calls = active = maximum = 0
    async def respond(request):
        nonlocal calls, active, maximum
        calls += 1
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return httpx.Response(200, json={"ok": True})
    client = BinanceMarketClient(Settings(), transport=httpx.MockTransport(respond))
    try:
        await asyncio.gather(*(client._get("/test", {}, key) for key in ("a", "a", "b")))
        assert calls == 2 and maximum == 2
    finally:
        await client.close()
