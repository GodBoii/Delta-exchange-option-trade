import httpx
import pytest

from app.config import Settings
from app.delta_context import DeltaMarketContextClient


@pytest.mark.asyncio
async def test_normalizes_public_delta_derivative_context() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/tickers/BTCUSD"
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "symbol": "BTCUSD",
                    "contract_type": "perpetual_futures",
                    "close": 64_806.0,
                    "mark_price": "64805.6",
                    "spot_price": "64779.3",
                    "oi": "1129.21",
                    "oi_value_usd": "73387357.9",
                    "volume": 3832.05,
                    "turnover_usd": 248858668.3,
                    "funding_rate": "0.01",
                    "timestamp": 1_786_275_818_200_315,
                    "quotes": {
                        "best_bid": "64805.5",
                        "best_ask": "64806.0",
                        "bid_size": "4311",
                        "ask_size": "1079",
                    },
                },
            },
        )

    client = DeltaMarketContextClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        result = await client.ticker()
    finally:
        await client.close()

    assert result["symbol"] == "BTCUSD"
    assert result["instrumentType"] == "perpetual_futures"
    assert result["openInterestBtc"] == 1129.21
    assert result["openInterestUsd"] == 73_387_357.9
    assert result["fundingRatePercent"] == 0.01
    assert result["bestBid"] == 64_805.5
    assert result["bestAsk"] == 64_806.0
    assert result["exchangeTimestamp"] == 1_786_275_818_200
