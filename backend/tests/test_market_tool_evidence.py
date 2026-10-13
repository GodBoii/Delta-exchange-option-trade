import json
from datetime import UTC, datetime

import httpx
import pytest

from automation_agent.assets import PROFILES
from automation_agent.market import MarketIntelligenceTools


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_market_tool_collects_rich_evidence_once_and_keeps_options_private(monkeypatch, asset):
    now = int(datetime.now(UTC).timestamp() * 1000)
    requests = []
    enrichment = {
        "asset": asset,
        "schemaVersion": 1,
        "baselines": {"lookbackDays": 90, "samples": 120, "sidewaysScore": {"mean": 70}},
        "liquidity": {"bands": {"0.1": {"bidBase": 2, "askBase": 3, "complete": False}}},
        "previousLiquidityBucket": {"samples": 597, "coveragePercent": 99.5},
        "futures": {"Binance": {"observedAt": now, "fundingPercent": 0.01, "oiBase": 100}},
        "options": [{"expiry": "2026-10-14T12:00:00Z", "atmIvPercent": 30}],
    }

    def respond(request):
        requests.append(request)
        assert request.url.path.startswith(f"/api/market/{asset.lower()}usd")
        if request.url.path.endswith("/agent-summary"):
            return httpx.Response(200, json=enrichment)
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"observations": []})
        if request.url.path.endswith("/option-catalogue"):
            return httpx.Response(200, json={"underlying": asset, "options": []})
        return httpx.Response(200, json={
            "symbol": f"{asset}USDT", "deltaContext": {"symbol": f"{asset}USD"},
            "ticker": {"lastPrice": 100}, "candles": [],
            "analysis": {
                "vwap": 99, "atr": {"value": 1}, "sidewaysProbability": 70,
                "marketStructure": {"ema20": 98, "ema50": 97, "state": "bullish"},
                "historicalVolatility": {"annualizedPercent": 40},
                "cvd": {"baseVolume": 2, "windowSeconds": 900},
            },
            "orderBook": {"bids": [[99, 2]], "asks": [[101, 3]]},
            "recentTrades": [{"time": now, "side": "buy", "quoteQuantity": 12}],
            "realtime": {"connected": True, "eventAgeMs": 90},
        })

    client = httpx.Client
    monkeypatch.setattr("automation_agent.market.httpx.Client", lambda **kw: client(
        transport=httpx.MockTransport(respond), **kw,
    ))
    toolkit = MarketIntelligenceTools(asset=PROFILES[asset], binance_url="http://market.test")
    packet = toolkit.collect_market_packet()
    count = len(requests)
    tool = toolkit.get_eth_market_packet if asset == "ETH" else toolkit.get_btc_market_packet
    result = json.loads(tool())
    assert json.loads(tool()) == result
    assert len(requests) == count
    assert result["historicalBaselines"] == enrichment["baselines"]
    assert result["liquidity"] == enrichment["liquidity"]
    assert result["previousLiquidityBucket"] == enrichment["previousLiquidityBucket"]
    assert result["futures"] == enrichment["futures"]
    assert result["computedAnalysis"]["emaIndicators"] == {"ema20": 98, "ema50": 97}
    assert result["computedAnalysis"]["vwapDistancePercent"] == pytest.approx((100 / 99 - 1) * 100)
    assert result["recentTradeFlow"]["start"] == result["recentTradeFlow"]["end"] == now
    assert result["realtime"]["eventAgeMs"] == 90
    assert result["units"]["cvdWindowSeconds"] == 900
    assert "atmIvPercent" not in json.dumps(result)
    assert packet["enrichment"]["options"] == enrichment["options"]
    assert toolkit.collect_delta_option_context() == {"underlying": asset, "options": []}


@pytest.mark.parametrize("summary", [[], {"asset": "ETH", "schemaVersion": 1}, {"asset": "BTC", "schemaVersion": 2}])
def test_invalid_enrichment_is_rejected_without_caching_partial_packet(monkeypatch, summary):
    def respond(request):
        if request.url.path.endswith("/agent-summary"):
            return httpx.Response(200, json=summary)
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"observations": []})
        return httpx.Response(200, json={
            "symbol": "BTCUSDT", "deltaContext": {"symbol": "BTCUSD"}, "candles": [], "analysis": {},
        })

    client = httpx.Client
    monkeypatch.setattr("automation_agent.market.httpx.Client", lambda **kw: client(
        transport=httpx.MockTransport(respond), **kw,
    ))
    toolkit = MarketIntelligenceTools(binance_url="http://market.test")
    with pytest.raises(ValueError, match="Invalid asset market summary"):
        toolkit.collect_market_packet()
    assert toolkit._packet_cache is None
