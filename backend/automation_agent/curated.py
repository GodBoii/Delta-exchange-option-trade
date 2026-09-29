"""One numerical starting input, without market arrays or repeated chart statistics."""

import json
import os
from copy import deepcopy
from typing import Any

from .market import compact_btc_market_packet


def enabled() -> bool:
    return os.getenv("CURATED_AGENT_INPUT_ENABLED", "false").strip().lower() == "true"


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def market_input(packet: dict, asset: str) -> dict:
    legacy = compact_btc_market_packet(packet, asset)
    analysis = deepcopy(legacy["computedAnalysis"])
    order_book = analysis.pop("orderBook", {})
    cvd = analysis.pop("cvd", {})
    ticker = legacy["ticker"]
    price = ticker.get("lastPrice")
    vwap = analysis.pop("vwap", None)
    volatility = analysis.pop("historicalVolatility", {})
    enrichment = packet.get("enrichment") or {}
    trade_times = [r["time"] for r in packet.get("recentTrades") or [] if isinstance(r.get("time"), (int, float))]
    realtime = packet.get("realtime") or {}
    spot = {
        "price": price,
        "change24hPercent": ticker.get("priceChangePercent"),
        "ema": analysis.pop("emaIndicators", {}),
        "atr": analysis.pop("atr", {}),
        "vwapDistancePercent": (price / vwap - 1) * 100 if price and vwap else None,
        "lastEventAt": realtime.get("lastEventAt"),
        "eventAgeMs": realtime.get("eventAgeMs"),
        "connected": realtime.get("connected"),
        "bookSynced": realtime.get("bookSynced"),
    }
    bids = (packet.get("orderBook") or {}).get("bids") or []
    asks = (packet.get("orderBook") or {}).get("asks") or []
    liquidity = enrichment.get("liquidity") or {
        "spreadBps": order_book.get("spreadBps"),
        "displayedBidBase": sum(r[1] for r in bids),
        "displayedAskBase": sum(r[1] for r in asks),
        "bidLevels": len(bids),
        "askLevels": len(asks),
    }
    return {
        "schemaVersion": 1,
        "asset": asset,
        "asOf": packet.get("capturedAt"),
        "units": {
            "spot": "USDT",
            "optionPremium": "USD per base asset",
            "optionCost": "USD",
            "volumeAndOi": "base asset unless explicitly contracts/quote",
            "iv": "annualized percent",
            "rvWindow": "120 one-minute returns",
            "sidewaysWindow": "60 minute closes; score, not forecast",
            "cvdWindow": "15 minutes",
            "funding": "percent per stated venue interval",
        },
        "spot": spot,
        "history": {
            "sidewaysScore": analysis.pop("sidewaysProbability", None),
            "realizedVolatilityAnnualizedPercent": volatility.get("annualizedPercent"),
            "realizedVolatilitySamples": volatility.get("sampleSize"),
            "comparisons": legacy["sessionHistory"],
            "baselines": enrichment.get("baselines", {}),
        },
        "timeframes": legacy["timeframes"],
        "flow": {
            "cvdBase": cvd.get("baseVolume"),
            "recentSample": {
                **legacy["recentTradeFlow"],
                "start": min(trade_times) if trade_times else None,
                "end": max(trade_times) if trade_times else None,
            },
        },
        "liquidity": {"current": liquidity, "lastBucket": enrichment.get("previousLiquidityBucket", {})},
        "futures": enrichment.get("futures", {}),
        "options": enrichment.get("options", []),
    }


def chart_notes(context: dict[str, dict]) -> dict[str, dict]:
    return {
        key: {
            name: value
            for name, value in row.items()
            if name in {"title", "source", "capturedAt", "xAxis", "yAxis", "readingNotes", "legend"}
        }
        for key, row in context.items()
    }
