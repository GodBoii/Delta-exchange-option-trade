"""Public Binance spot candles for descriptive event studies. No trading API."""

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

MINUTE = 60_000
SYMBOLS = {
    asset: asset + "USDT" for asset in ("BTC", "ETH", "SOL", "BNB", "DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI")
}


class MarketError(Exception):
    def __init__(self, code: str, retry_seconds: int = 60):
        super().__init__(code)
        self.code, self.retry_seconds = code, retry_seconds


@dataclass(frozen=True)
class Candle:
    opened_ms: int
    closed_ms: int
    close: float
    volume: float


def baseline_open(at: datetime) -> int:
    """Use the last fully closed minute before the event, never its containing candle."""
    return int(at.timestamp() * 1000) // MINUTE * MINUTE - MINUTE


def parse_candles(data: object, start: int, end: int) -> list[Candle]:
    if not isinstance(data, list) or len(data) > 100:
        raise MarketError("invalid_market_response")
    candles = []
    try:
        for row in data:
            if not isinstance(row, list) or len(row) < 7 or type(row[0]) is not int or type(row[6]) is not int:
                raise ValueError("invalid candle")
            candle = Candle(row[0], row[6], float(row[4]), float(row[5]))
            if (
                candle.opened_ms % MINUTE
                or candle.closed_ms != candle.opened_ms + MINUTE - 1
                or not math.isfinite(candle.close)
                or candle.close <= 0
                or not math.isfinite(candle.volume)
                or candle.volume < 0
            ):
                raise ValueError("invalid candle")
            candles.append(candle)
    except (ValueError, TypeError, OverflowError) as error:
        raise MarketError("invalid_market_response") from error
    if [c.opened_ms for c in candles] != list(range(start, end + 1, MINUTE)):
        raise MarketError("missing_or_unordered_candles")
    return candles


class BinanceCandles:
    def __init__(self):
        self.last_request = float("-inf")

    def fetch(self, asset: str, start: int, end: int) -> list[Candle]:
        if asset not in SYMBOLS or end < start or (end - start) // MINUTE >= 100:
            raise MarketError("unsupported_market_window")
        delay = 0.5 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        query = urlencode(
            {"symbol": SYMBOLS[asset], "interval": "1m", "startTime": start, "endTime": end + MINUTE - 1, "limit": 100}
        )
        try:
            with urlopen("https://data-api.binance.vision/api/v3/klines?" + query, timeout=10) as response:
                body = response.read(200_001)
                if len(body) > 200_000:
                    raise MarketError("oversized_market_response")
                return parse_candles(json.loads(body), start, end)
        except HTTPError as error:
            if error.code in {418, 429}:
                retry = error.headers.get("Retry-After", "300")
                raise MarketError("market_rate_limited", max(300, int(retry) if retry.isdigit() else 300)) from error
            raise MarketError(f"market_http_{error.code}", 3600 if error.code in {400, 403, 451} else 60) from error
        except (OSError, ValueError) as error:
            raise MarketError("market_transport_or_json_error") from error


def measure(candles: list[Candle], published: datetime, received: datetime, horizon: int) -> dict:
    by_open = {c.opened_ms: c for c in candles}
    pub, receipt = baseline_open(published), baseline_open(received)
    points = {
        "pre_publication_5m": pub - 5 * MINUTE,
        "publication": pub,
        "receipt": receipt,
        "publication_horizon": pub + horizon * MINUTE,
        "receipt_horizon": receipt + horizon * MINUTE,
    }
    try:
        selected = {name: by_open[opened] for name, opened in points.items()}
    except KeyError as error:
        raise MarketError("missing_anchor_candle") from error
    if any(c.volume == 0 for c in selected.values()):
        raise MarketError("stale_no_trade_anchor")

    def change(before: str, after: str) -> float:
        return round((selected[after].close / selected[before].close - 1) * 100, 8)

    return {
        "pre_publication_5m_pct": change("pre_publication_5m", "publication"),
        "publication_to_receipt_pct": change("publication", "receipt"),
        "publication_return_pct": change("publication", "publication_horizon"),
        "receipt_return_pct": change("receipt", "receipt_horizon"),
        "anchors": {name: {"close": c.close, "closed_ms": c.closed_ms} for name, c in selected.items()},
        "candles": [[c.opened_ms, c.closed_ms, c.close, c.volume] for c in candles],
    }
