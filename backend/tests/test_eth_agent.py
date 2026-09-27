"""The ETH agent mirrors the BTC agent without sharing its schedule, strategies, or market."""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.assets import asset_run_key, enabled_assets, parse_asset, run_asset, strategy_asset
from app.automation import AutomationScheduler
from app.default_strategies import builtin_strategy_definitions, default_strategy_definitions, eth_strategy_definitions
from automation_agent import team
from automation_agent.assets import PROFILES, asset_profile
from automation_agent.market import MarketIntelligenceTools, compact_btc_market_packet
from news_analyzer.main import AutomationAnalysisRequest

NOW = datetime(2026, 9, 27, 6, tzinfo=UTC)


def test_eth_builtins_are_six_separate_atm_strategies():
    eth = eth_strategy_definitions(NOW)

    assert [item.name for item in eth] == [
        "ETH Long call",
        "ETH Long put",
        "ETH Long ATM straddle",
        "ETH Short ATM straddle",
        "ETH Long ATM straddle - next-day expiry",
        "ETH Short ATM straddle - next-day expiry",
    ]
    assert all(item.instrument.index == "ETHUSD" and item.instrument.underlying == "ETH" for item in eth)
    assert all(leg.strikeMode == "atm" for item in eth for leg in item.legs)
    assert all("BTC" not in item.description for item in eth)
    # The BTC catalog is untouched and the seed list carries both.
    assert len(default_strategy_definitions(NOW)) == 15
    assert all(item.instrument.underlying == "BTC" for item in default_strategy_definitions(NOW))
    assert len(builtin_strategy_definitions(NOW)) == 21


def test_asset_helpers_keep_legacy_btc_rows_and_prefix_other_assets():
    assert run_asset({}) == "BTC"
    assert run_asset({"asset": "eth"}) == "ETH"
    assert strategy_asset({"definition_json": {"instrument": {"underlying": "ETH"}}}) == "ETH"
    assert asset_run_key("BTC", "london_session:2026-09-27") == "london_session:2026-09-27"
    assert asset_run_key("ETH", "london_session:2026-09-27") == "ETH:london_session:2026-09-27"
    assert enabled_assets(SimpleNamespace(automation_assets="ETH, BTC")) == ("ETH", "BTC")
    assert enabled_assets(SimpleNamespace()) == ("BTC", "ETH")
    with pytest.raises(ValueError):
        parse_asset("SOL")


@pytest.mark.asyncio
async def test_scheduler_creates_a_fixed_review_per_asset_with_distinct_keys():
    class Database:
        settings = SimpleNamespace(shared_analysis_enabled=False, automation_assets="BTC,ETH")
        payload: list[dict] = []

        async def select(self, _table: str, _params: dict) -> list[dict]:
            return [{"user_id": "user-1"}]

        async def rpc(self, function: str, payload: dict) -> int:
            if function == "ensure_automation_fixed_runs":
                self.payload = payload["p_runs"]
            return 0

    database = Database()
    await AutomationScheduler(database, object())._enqueue_session_reviews()  # type: ignore[arg-type]

    btc = [row for row in database.payload if row["asset"] == "BTC"]
    eth = [row for row in database.payload if row["asset"] == "ETH"]
    assert btc and len(btc) == len(eth)
    assert {row["trigger"] for row in btc} == {row["trigger"] for row in eth}
    assert all(not row["run_key"].startswith("ETH:") for row in btc)
    assert all(row["run_key"].startswith("ETH:") for row in eth)
    assert len({row["run_key"] for row in database.payload}) == len(database.payload)


def test_eth_market_tool_reads_the_eth_market_service_and_delta_eth_options(monkeypatch):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"available": True, "observations": []})
        if request.url.path == "/v2/tickers":
            assert request.url.params["underlying_asset_symbols"] == "ETH"
            return httpx.Response(200, json={"result": []})
        return httpx.Response(200, json={"symbol": "ETHUSDT", "candles": [], "analysis": {}})

    client = httpx.Client
    monkeypatch.setattr(
        "automation_agent.market.httpx.Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    tools = MarketIntelligenceTools(asset=PROFILES["ETH"], binance_url="http://eth.test", delta_url="http://delta.test")

    assert set(tools.functions) == {"get_eth_market_packet"}
    packet = tools.collect_market_packet()
    assert packet["symbol"] == "ETHUSDT"
    assert tools.collect_delta_option_context()["underlying"] == "ETH"
    assert all("/api/market/ethusd" in url for url in requested if "eth.test" in url)
    assert any(url.endswith("/api/market/ethusd/history") for url in requested)


def test_eth_packet_and_charts_use_eth_units():
    packet = {"orderBook": {"bids": [[2690, 3]], "asks": [[2691, 1]]}, "timeframes": {}}
    compact = compact_btc_market_packet(packet, "ETH")

    assert compact["spotOrderBook"]["bidDepthTop20Eth"] == 3
    assert "bidDepthTop20Btc" not in compact["spotOrderBook"]
    assert team._asset_specific_instructions("BTC") == []
    assert any("ETH" in text for text in team._asset_specific_instructions("ETH"))


def test_analysis_request_defaults_to_btc_and_accepts_eth():
    base = {
        "userId": "global",
        "agentRunId": "22222222-2222-4222-8222-222222222222",
        "sessionId": "scheduled-1",
        "accountContext": {},
        "trigger": "london_session",
    }
    assert AutomationAnalysisRequest(**base).asset == "BTC"
    assert AutomationAnalysisRequest(**base, asset="ETH").asset == "ETH"
    assert asset_profile("ETH").agent_id == "eth-strategy-automation-team"
    assert asset_profile(None).agent_id == "btc-strategy-automation-team"
