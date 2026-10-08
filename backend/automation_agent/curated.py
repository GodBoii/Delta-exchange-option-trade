"""One numerical starting input, without market arrays or repeated chart statistics."""

import json
import math
import os
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.exit_schedule import maximum_hold_hours, session_expiry

from .market import compact_btc_market_packet


def enabled() -> bool:
    return os.getenv("CURATED_AGENT_INPUT_ENABLED", "false").strip().lower() == "true"


def dumps(value: Any) -> str:
    def display(item):
        if isinstance(item, float):
            return float(f"{item:.8g}")
        if isinstance(item, dict):
            return {key: display(value) for key, value in item.items()}
        if isinstance(item, list):
            return [display(value) for value in item]
        return item

    return json.dumps(display(value), separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def market_input(packet: dict, asset: str, trigger: str | None = None) -> dict:
    legacy = compact_btc_market_packet(packet, asset)
    analysis = deepcopy(legacy["computedAnalysis"])
    order_book = analysis.pop("orderBook", {})
    cvd = analysis.pop("cvd", {})
    ticker = legacy["ticker"]
    price = ticker.get("lastPrice")
    vwap = analysis.pop("vwap", None)
    volatility = analysis.pop("historicalVolatility", {})
    enrichment = packet.get("enrichment") or {}
    captured = (
        datetime.fromtimestamp(packet["capturedAt"] / 1000, UTC)
        if packet.get("capturedAt") else datetime.now(UTC)
    )
    expiry = session_expiry(captured)
    option_rows = []
    for row in enrichment.get("options", []):
        try:
            observed_expiry = datetime.fromisoformat(str(row.get("expiry", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if observed_expiry == expiry:
            option_rows.append(row)
    holding_scales = []
    for preset in ([7, 11] if maximum_hold_hours(trigger) == 11 else [7]):
        hours = max(0, min(preset, (expiry.timestamp() - captured.timestamp() - 300) / 3600))
        factor = math.sqrt(hours / (365 * 24))
        reference = option_rows[0] if option_rows and option_rows[0].get("atmIvPercent") is not None else None
        rv = volatility.get("annualizedPercent")
        holding_scales.append(
            {
                "hours": hours,
                "presetHours": preset,
                "realizedScaledMovePercent": rv * factor if rv is not None else None,
                "impliedScaledMovePercent": reference["atmIvPercent"] * factor if reference else None,
                "referenceExpiry": reference["expiry"] if reference else None,
            }
        )
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
            "ivScaledMove": "IV times sqrt(days/365), a scale estimate, not a price forecast",
            "rvWindow": "120 one-minute returns",
            "sidewaysWindow": "60 minute closes; score, not forecast",
            "cvdWindowSeconds": cvd.get("windowSeconds", 900),
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
        "options": option_rows,
        "sessionPolicy": {
            "expiryUtc": expiry.isoformat(), "maximumHoldHours": maximum_hold_hours(trigger),
            "remainingMinutes": max(0, (expiry.timestamp() - captured.timestamp()) / 60),
        },
        "holdingMoveScales": {
            "basis": "Indicative ATM scale for the upcoming expiry; duration uses snapshot time and a five-minute "
            "buffer. Preview supplies the actual selected-leg scales and saved buffer. Not forecasts or probabilities.",
            "rows": holding_scales,
        },
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
