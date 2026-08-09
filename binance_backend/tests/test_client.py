import json

import httpx
import pytest

from app.client import BinanceMarketClient
from app.config import Settings


@pytest.mark.asyncio
async def test_normalizes_ticker_and_candles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ticker/24hr"):
            return httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT", "lastPrice": "64000.1", "priceChange": "100.1",
                    "priceChangePercent": "0.157", "openPrice": "63900", "highPrice": "64500",
                    "lowPrice": "63000", "weightedAvgPrice": "63880", "volume": "2500.5",
                    "quoteVolume": "160000000", "count": 99, "openTime": 1000, "closeTime": 2000,
                },
            )
        return httpx.Response(
            200,
            json=[
                [
                    1000, "63000", "64500", "62500", "64000.1", "100", 2000,
                    "6400000", 9, "60", "3840000", "0",
                ]
            ],
        )

    client = BinanceMarketClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        ticker = await client.ticker()
        candles = await client.candles("1h", 20)
    finally:
        await client.close()

    assert ticker["lastPrice"] == 64000.1
    assert ticker["tradeCount"] == 99
    assert ticker["baseVolume"] == 2500.5
    assert ticker["quoteVolume"] == 160000000.0
    assert candles[0]["open"] == 63000.0
    assert candles[0]["close"] == 64000.1
    assert candles[0]["baseVolume"] == 100.0
    assert candles[0]["quoteVolume"] == 6400000.0


@pytest.mark.asyncio
async def test_reuses_short_lived_market_cache() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=json.dumps([]), headers={"Content-Type": "application/json"})

    client = BinanceMarketClient(Settings(market_cache_seconds=10), transport=httpx.MockTransport(handler))
    try:
        await client.candles("5m", 20)
        await client.candles("5m", 20)
    finally:
        await client.close()

    assert requests == 1
