import math
import statistics
import time
from typing import Any


def calculate_analysis(
    candles: list[dict[str, Any]],
    bids: list[list[float]],
    asks: list[list[float]],
    cumulative_volume_delta: float,
) -> dict[str, Any]:
    valid = [candle for candle in candles if candle.get("close", 0) > 0]
    closes = [float(candle["close"]) for candle in valid]
    last_price = closes[-1] if closes else 0.0
    atr_value = atr(valid, 14)
    historical_volatility = annualized_historical_volatility(closes)
    vwap_value = vwap(valid)
    bid_depth = sum(level[1] for level in bids[:20])
    ask_depth = sum(level[1] for level in asks[:20])
    total_depth = bid_depth + ask_depth
    imbalance = ((bid_depth - ask_depth) / total_depth) if total_depth else 0.0
    best_bid = bids[0][0] if bids else 0.0
    best_ask = asks[0][0] if asks else 0.0
    spread = max(0.0, best_ask - best_bid) if best_bid and best_ask else 0.0
    midpoint = (best_ask + best_bid) / 2 if best_bid and best_ask else last_price
    structure = market_structure(closes)
    sideways = sideways_probability(closes, vwap_value)

    return {
        "computedAt": int(time.time() * 1000),
        "interval": "1m",
        "atr": {
            "value": atr_value,
            "percent": (atr_value / last_price * 100) if last_price else 0.0,
            "period": 14,
        },
        "historicalVolatility": {
            "annualizedPercent": historical_volatility,
            "sampleSize": max(0, min(len(closes) - 1, 120)),
        },
        "vwap": vwap_value,
        "cvd": {
            "baseVolume": cumulative_volume_delta,
            "window": "15m",
        },
        "orderBook": {
            "bestBid": best_bid,
            "bestAsk": best_ask,
            "spread": spread,
            "spreadBps": (spread / midpoint * 10_000) if midpoint else 0.0,
            "bidDepth": bid_depth,
            "askDepth": ask_depth,
            "imbalance": imbalance,
            "demandLevel": strongest_level(bids[:50]),
            "supplyLevel": strongest_level(asks[:50]),
        },
        "marketStructure": structure,
        "sidewaysProbability": sideways,
    }


def atr(candles: list[dict[str, Any]], period: int) -> float:
    if len(candles) < 2:
        return 0.0
    window = candles[-(period + 1) :]
    ranges: list[float] = []
    for previous, current in zip(window, window[1:], strict=False):
        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(ranges) / len(ranges) if ranges else 0.0


def annualized_historical_volatility(closes: list[float]) -> float:
    sample = closes[-121:]
    if len(sample) < 3:
        return 0.0
    returns = [
        math.log(current / previous)
        for previous, current in zip(sample, sample[1:], strict=False)
        if previous > 0
    ]
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * math.sqrt(525_600) * 100


def vwap(candles: list[dict[str, Any]]) -> float:
    sample = candles[-240:]
    weighted = 0.0
    volume = 0.0
    for candle in sample:
        candle_volume = float(candle.get("baseVolume") or 0)
        typical_price = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        weighted += typical_price * candle_volume
        volume += candle_volume
    return weighted / volume if volume else 0.0


def market_structure(closes: list[float]) -> dict[str, Any]:
    if len(closes) < 50:
        return {"state": "forming", "strength": 0.0, "ema20": 0.0, "ema50": 0.0}
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    last = closes[-1]
    separation = abs(ema20 - ema50) / last * 100 if last else 0.0
    if ema20 > ema50 and last > ema20:
        state = "bullish"
    elif ema20 < ema50 and last < ema20:
        state = "bearish"
    else:
        state = "ranging"
    return {"state": state, "strength": min(100.0, separation * 200), "ema20": ema20, "ema50": ema50}


def sideways_probability(closes: list[float], vwap_value: float) -> float:
    sample = closes[-60:]
    if len(sample) < 10:
        return 0.0
    path = sum(abs(current - previous) for previous, current in zip(sample, sample[1:], strict=False))
    efficiency = abs(sample[-1] - sample[0]) / path if path else 0.0
    range_percent = (max(sample) - min(sample)) / sample[-1] * 100 if sample[-1] else 0.0
    vwap_deviation = abs(sample[-1] - vwap_value) / sample[-1] * 100 if sample[-1] and vwap_value else 0.0
    score = (
        (1 - min(1.0, efficiency)) * 55
        + (1 - min(1.0, range_percent / 1.5)) * 25
        + (1 - min(1.0, vwap_deviation / 0.5)) * 20
    )
    return max(0.0, min(100.0, score))


def ema(values: list[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


def strongest_level(levels: list[list[float]]) -> dict[str, float] | None:
    if not levels:
        return None
    price, quantity = max(levels, key=lambda level: level[1])
    return {"price": price, "quantity": quantity}
