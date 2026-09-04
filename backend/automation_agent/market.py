from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time
from typing import Any
from urllib.parse import quote

import httpx
from agno.tools import Toolkit

TIMEFRAMES: tuple[tuple[str, str, int], ...] = (
    ("1 minute", "1m", 160),
    ("15 minute", "15m", 160),
    ("1 day", "1d", 120),
)


class MarketIntelligenceTools(Toolkit):
    def __init__(self, *, binance_url: str | None = None, delta_url: str | None = None, **kwargs: Any) -> None:
        self.binance_url = (binance_url or os.getenv("BINANCE_INTERNAL_URL") or "http://binace:8001").rstrip("/")
        self.delta_url = (delta_url or os.getenv("DELTA_PUBLIC_BASE_URL") or "https://api.india.delta.exchange").rstrip(
            "/"
        )
        self._btc_cache: dict[str, Any] | None = None
        super().__init__(
            name="market_intelligence_tools",
            tools=[self.get_btc_market_packet],
            instructions="Use Binance Spot data for BTC direction, volume, volatility, and order-flow analysis.",
            add_instructions=True,
            **kwargs,
        )

    def get_btc_market_packet(self) -> str:
        """Return compact BTC Spot trend, flow, volatility, order-book, and timeframe summaries."""
        packet = self._btc_cache or self.collect_btc_market_packet()
        return json.dumps(compact_btc_market_packet(packet), ensure_ascii=False, default=str)

    def collect_btc_market_packet(self) -> dict[str, Any]:
        def load(spec: tuple[str, str, int]) -> tuple[str, dict[str, Any]]:
            label, interval, limit = spec
            with httpx.Client(timeout=httpx.Timeout(20, connect=5)) as client:
                response = client.get(
                    f"{self.binance_url}/api/market/btcusd",
                    params={"interval": interval, "limit": limit},
                )
                response.raise_for_status()
                return label, response.json()

        with ThreadPoolExecutor(max_workers=len(TIMEFRAMES)) as executor:
            loaded = dict(executor.map(load, TIMEFRAMES))

        primary = loaded["15 minute"]
        packet = {
            "capturedAt": int(datetime.now(UTC).timestamp() * 1000),
            "source": "Binance Spot",
            "symbol": primary.get("symbol"),
            "ticker": primary.get("ticker"),
            "analysis": primary.get("analysis"),
            "orderBook": primary.get("orderBook"),
            "recentTrades": primary.get("recentTrades"),
            "realtime": primary.get("realtime"),
            "timeframes": {
                label: {
                    "interval": payload.get("interval"),
                    "candles": payload.get("candles") or [],
                    "summary": summarize_candles(payload.get("candles") or []),
                }
                for label, payload in loaded.items()
            },
        }
        self._btc_cache = packet
        return packet

    def collect_delta_option_context(self) -> dict[str, Any]:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5)) as client:
            response = client.get(
                f"{self.delta_url}/v2/tickers",
                params={
                    "contract_types": "call_options,put_options",
                    "underlying_asset_symbols": "BTC",
                    "page_size": 100,
                },
                headers={"Accept": "application/json", "User-Agent": "trade-cognition-automation/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("result") if isinstance(payload, dict) else None
            options = [compact_option(row) for row in rows or [] if isinstance(row, dict)]
            representatives: dict[str, str] = {}
            for option in options:
                symbol = str(option.get("symbol") or "")
                code = _expiry_code(symbol)
                if code:
                    representatives.setdefault(code, symbol)
            settlements: dict[str, str] = {}
            for code, symbol in representatives.items():
                try:
                    product_response = client.get(f"{self.delta_url}/v2/products/{quote(symbol, safe='')}")
                    product_response.raise_for_status()
                    product = product_response.json().get("result") or {}
                    settlement = product.get("settlement_time")
                except (httpx.HTTPError, ValueError, AttributeError):
                    settlement = None
                settlements[code] = str(settlement or _expiry_from_symbol(symbol) or "")
            for option in options:
                code = _expiry_code(str(option.get("symbol") or ""))
                option["expiry"] = settlements.get(code or "") or option.get("expiry")
        options.sort(key=lambda item: (str(item.get("expiry")), _number(item.get("strike"))))
        context = {"source": "Delta Exchange", "underlying": "BTC", "count": len(options), "options": options}
        return context


def summarize_candles(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if not candles:
        return {"available": False}
    first = candles[0]
    last = candles[-1]
    first_close = _number(first.get("close"))
    last_close = _number(last.get("close"))
    highs = [_number(candle.get("high")) for candle in candles]
    lows = [_number(candle.get("low")) for candle in candles]
    volumes = [_number(candle.get("volume") or candle.get("baseVolume")) for candle in candles]
    return {
        "available": True,
        "candles": len(candles),
        "firstOpenTime": first.get("openTime"),
        "lastCloseTime": last.get("closeTime"),
        "open": _number(first.get("open")),
        "close": last_close,
        "returnPercent": ((last_close / first_close) - 1) * 100 if first_close else None,
        "high": max(highs),
        "low": min(lows),
        "volume": sum(volumes),
    }


def compact_btc_market_packet(packet: dict[str, Any]) -> dict[str, Any]:
    ticker = packet.get("ticker") or {}
    order_book = packet.get("orderBook") or {}
    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    bid_depth = sum(_number(level[1]) for level in bids[:20] if isinstance(level, list) and len(level) >= 2)
    ask_depth = sum(_number(level[1]) for level in asks[:20] if isinstance(level, list) and len(level) >= 2)
    total_depth = bid_depth + ask_depth
    trades = packet.get("recentTrades") or []
    buy_volume = sum(
        _number(trade.get("quoteQuantity"))
        for trade in trades
        if isinstance(trade, dict) and trade.get("side") == "buy"
    )
    sell_volume = sum(
        _number(trade.get("quoteQuantity"))
        for trade in trades
        if isinstance(trade, dict) and trade.get("side") == "sell"
    )
    return {
        "capturedAt": packet.get("capturedAt"),
        "source": packet.get("source"),
        "symbol": packet.get("symbol"),
        "realtime": packet.get("realtime"),
        "ticker": _pick(
            ticker,
            "lastPrice",
            "priceChange",
            "priceChangePercent",
            "highPrice",
            "lowPrice",
            "baseVolume",
            "quoteVolume",
            "bestBid",
            "bestAsk",
        ),
        "computedAnalysis": packet.get("analysis"),
        "timeframes": {label: payload.get("summary") for label, payload in (packet.get("timeframes") or {}).items()},
        "spotOrderBook": {
            "bestBid": bids[0][0] if bids else None,
            "bestAsk": asks[0][0] if asks else None,
            "bidDepthTop20Btc": bid_depth,
            "askDepthTop20Btc": ask_depth,
            "imbalance": (bid_depth - ask_depth) / total_depth if total_depth else 0,
        },
        "recentTradeFlow": {
            "sampleSize": len(trades),
            "buyQuoteVolume": buy_volume,
            "sellQuoteVolume": sell_volume,
            "netBuyQuoteVolume": buy_volume - sell_volume,
        },
    }


def compact_option(row: dict[str, Any]) -> dict[str, Any]:
    quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
    greeks = row.get("greeks") if isinstance(row.get("greeks"), dict) else {}
    return {
        "symbol": row.get("symbol"),
        "type": row.get("contract_type"),
        "expiry": row.get("settlement_time") or row.get("expiry_date"),
        "strike": row.get("strike_price"),
        "spot": row.get("spot_price"),
        "mark": row.get("mark_price"),
        "bestBid": quotes.get("best_bid"),
        "bestAsk": quotes.get("best_ask"),
        "bidSize": quotes.get("bid_size"),
        "askSize": quotes.get("ask_size"),
        "impliedVolatility": row.get("mark_vol") or row.get("iv"),
        "openInterest": row.get("oi"),
        "volume": row.get("volume"),
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
        "contractValue": row.get("contract_value"),
    }


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source.get(key) for key in keys if source.get(key) not in (None, "")}


def _expiry_code(symbol: str) -> str | None:
    match = re.search(r"-(\d{6})$", symbol)
    return match.group(1) if match else None


def _expiry_from_symbol(symbol: str) -> str | None:
    code = _expiry_code(symbol)
    if not code:
        return None
    try:
        expiry_date = datetime.strptime(code, "%d%m%y").date()
    except ValueError:
        return None
    return datetime.combine(expiry_date, time(12), tzinfo=UTC).isoformat()
