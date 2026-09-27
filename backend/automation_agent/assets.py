"""Per-asset settings for the automation agents. Both agents share every tool and rule."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.assets import Asset, parse_asset


@dataclass(frozen=True, slots=True)
class AssetProfile:
    code: Asset
    name: str
    delta_index: str
    spot_symbol: str
    market_route: str
    market_url_env: str
    default_market_url: str
    news_prompt: str
    news_focus_query: str
    agent_id: str
    recheck_agent_id: str

    @property
    def market_base_url(self) -> str:
        return (os.getenv(self.market_url_env) or self.default_market_url).rstrip("/")


PROFILES: dict[Asset, AssetProfile] = {
    "BTC": AssetProfile(
        code="BTC",
        name="Bitcoin",
        delta_index="BTCUSD",
        spot_symbol="BTCUSDT",
        market_route="btcusd",
        # BINANCE_INTERNAL_URL predates ETH and still configures the BTC market service.
        market_url_env="BINANCE_INTERNAL_URL",
        default_market_url="http://binace:8001",
        news_prompt="Bitcoin BTC market moving news and upcoming macroeconomic catalysts",
        news_focus_query="Bitcoin BTC ETF regulation latest news",
        agent_id="btc-strategy-automation-team",
        recheck_agent_id="btc-strategy-activation-recheck",
    ),
    "ETH": AssetProfile(
        code="ETH",
        name="Ethereum",
        delta_index="ETHUSD",
        spot_symbol="ETHUSDT",
        market_route="ethusd",
        market_url_env="BINANCE_ETH_INTERNAL_URL",
        default_market_url="http://binace-eth:8001",
        news_prompt="Ethereum ETH market moving news and upcoming macroeconomic catalysts",
        news_focus_query="Ethereum ETH ETF staking network upgrade regulation latest news",
        agent_id="eth-strategy-automation-team",
        recheck_agent_id="eth-strategy-activation-recheck",
    ),
}


def asset_profile(value: str | None) -> AssetProfile:
    return PROFILES[parse_asset(value)]
