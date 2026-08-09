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


@pytest.mark.asyncio
async def test_builds_complete_public_delta_market_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/tickers/BTCUSD":
            return httpx.Response(200, json={"success": True, "result": {
                "symbol": "BTCUSD", "close": 65_000, "mark_price": "64999", "spot_price": "64980",
                "oi": "1200", "oi_value_usd": "78000000", "oi_contracts": "1200000",
                "oi_change_usd_6h": "2500000", "funding_rate": "0.01", "ltp_change_24h": "1.2",
                "price_band": {"lower_limit": "61000", "upper_limit": "68000"},
                "quotes": {"best_bid": "64999.5", "best_ask": "65000", "bid_size": "10", "ask_size": "12"},
            }})
        if path == "/v2/l2orderbook/BTCUSD":
            return httpx.Response(200, json={"success": True, "result": {
                "symbol": "BTCUSD", "buy": [{"price": "64999.5", "size": 10, "depth": "10"}],
                "sell": [{"price": "65000", "size": 12, "depth": "12"}],
            }})
        if path == "/v2/trades/BTCUSD":
            return httpx.Response(200, json={"success": True, "result": [{
                "price": "65000", "size": 20, "timestamp": 1_786_275_818_200_000,
                "buyer_role": "taker", "seller_role": "maker",
            }]})
        if path == "/v2/products/BTCUSD":
            return httpx.Response(200, json={"success": True, "result": {
                "id": 27, "symbol": "BTCUSD", "description": "Bitcoin Perpetual", "state": "live",
                "trading_status": "operational", "contract_type": "perpetual_futures", "contract_value": "0.001",
                "tick_size": "0.5", "default_leverage": "200", "initial_margin": "0.5",
                "maintenance_margin": "0.25", "maker_commission_rate": "0.0002",
                "taker_commission_rate": "0.0005", "underlying_asset": {"symbol": "BTC"},
                "quoting_asset": {"symbol": "USD"}, "settling_asset": {"symbol": "USD"},
                "spot_index": {"symbol": ".DEXBTUSD", "description": "Bitcoin index"},
                "product_specs": {"rate_exchange_interval": 28800},
            }})
        if path == "/v2/history/candles":
            value = {"OI:BTCUSD": 1200, "FUNDING:BTCUSD": 0.01, "MARK:BTCUSD": 64999}[request.url.params["symbol"]]
            return httpx.Response(200, json={"success": True, "result": [
                {"time": 100, "open": value, "high": value, "low": value, "close": value},
                {"time": 200, "open": value, "high": value, "low": value, "close": value},
            ]})
        return httpx.Response(404)

    client = DeltaMarketContextClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        result = await client.snapshot(include_slow_data=True)
    finally:
        await client.close()

    assert result["product"]["productId"] == 27
    assert result["product"]["fundingIntervalHours"] == 8
    assert result["orderBook"]["bids"][0] == [64_999.5, 10.0, 10.0, 0.01]
    assert result["recentTrades"][0]["side"] == "buy"
    assert result["recentTrades"][0]["sizeBtc"] == pytest.approx(0.02)
    assert result["openInterestHistory"][0]["time"] == 100_000
    assert result["fundingHistory"][-1]["close"] == 0.01
    assert result["priceBandUpper"] == 68_000
