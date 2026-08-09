import asyncio
import time
from typing import Any

import httpx

from .config import Settings

INTERVALS = frozenset({"1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"})


class BinanceMarketError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class BinanceMarketClient:
    """Public Binance Spot REST client used for history and WebSocket recovery."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.binance_base_url,
            timeout=httpx.Timeout(10.0, connect=4.0),
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "delta-strategy-desk-spot-intelligence/2.0"},
        )
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(
        self,
        path: str,
        params: dict[str, Any],
        cache_key: str | None = None,
        cache_seconds: float | None = None,
    ) -> Any:
        now = time.monotonic()
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

        async with self._lock:
            now = time.monotonic()
            if cache_key:
                cached = self._cache.get(cache_key)
                if cached and cached[0] > now:
                    return cached[1]
            try:
                response = await self.http.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
            except httpx.TimeoutException as exc:
                raise BinanceMarketError("Binance Spot market data timed out") from exc
            except httpx.HTTPStatusError as exc:
                status = 503 if exc.response.status_code in {418, 429} else 502
                raise BinanceMarketError("Binance Spot market data is temporarily unavailable", status) from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise BinanceMarketError("Could not read Binance Spot market data") from exc

            if cache_key:
                ttl = cache_seconds if cache_seconds is not None else self.settings.market_cache_seconds
                self._cache[cache_key] = (now + ttl, payload)
            return payload

    async def ticker(self) -> dict[str, Any]:
        raw = await self._get(
            "/api/v3/ticker/24hr",
            {"symbol": self.settings.binance_symbol},
            "ticker",
        )
        if not isinstance(raw, dict) or not raw:
            raise BinanceMarketError("Binance returned no BTCUSDT ticker data")
        return normalize_ticker(raw)

    async def candles(
        self,
        interval: str,
        limit: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "symbol": self.settings.binance_symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        cache_key = f"candles:{interval}:{limit}:{start_time}:{end_time}"
        rows = await self._get("/api/v3/klines", params, cache_key)
        return [normalize_candle(row) for row in rows]

    async def order_book(self, limit: int, *, fresh: bool = False) -> dict[str, Any]:
        raw = await self._get(
            "/api/v3/depth",
            {"symbol": self.settings.binance_symbol, "limit": limit},
            None if fresh else f"depth:{limit}",
        )
        return {
            "lastUpdateId": int(raw.get("lastUpdateId") or 0),
            "eventTime": 0,
            "transactionTime": 0,
            "bids": [[number(level[0]), number(level[1])] for level in raw.get("bids", [])],
            "asks": [[number(level[0]), number(level[1])] for level in raw.get("asks", [])],
        }

    async def recent_trades(self, limit: int) -> list[dict[str, Any]]:
        rows = await self._get(
            "/api/v3/trades",
            {"symbol": self.settings.binance_symbol, "limit": limit},
            f"trades:{limit}",
        )
        return [
            {
                "id": int(row.get("id") or 0),
                "price": number(row.get("price")),
                "quantity": number(row.get("qty")),
                "quoteQuantity": number(row.get("quoteQty")),
                "time": int(row.get("time") or 0),
                "buyerIsMaker": bool(row.get("isBuyerMaker")),
            }
            for row in rows
        ]


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_ticker(raw: dict[str, Any]) -> dict[str, Any]:
    base_volume = number(raw.get("volume") or raw.get("v"))
    quote_volume = number(raw.get("quoteVolume") or raw.get("q"))
    return {
        "lastPrice": number(raw.get("lastPrice") or raw.get("c")),
        "priceChange": number(raw.get("priceChange") or raw.get("p")),
        "priceChangePercent": number(raw.get("priceChangePercent") or raw.get("P")),
        "openPrice": number(raw.get("openPrice") or raw.get("o")),
        "highPrice": number(raw.get("highPrice") or raw.get("h")),
        "lowPrice": number(raw.get("lowPrice") or raw.get("l")),
        "weightedAveragePrice": number(raw.get("weightedAvgPrice") or raw.get("w")),
        "contractVolume": base_volume,
        "baseVolume": base_volume,
        "quoteVolume": quote_volume,
        "tradeCount": int(raw.get("count") or raw.get("n") or 0),
        "openTime": int(raw.get("openTime") or raw.get("O") or 0),
        "closeTime": int(raw.get("closeTime") or raw.get("C") or raw.get("E") or 0),
    }


def normalize_candle(row: list[Any]) -> dict[str, Any]:
    base_volume = number(row[5])
    return {
        "openTime": int(row[0]),
        "open": number(row[1]),
        "high": number(row[2]),
        "low": number(row[3]),
        "close": number(row[4]),
        "contractVolume": base_volume,
        "baseVolume": base_volume,
        "quoteVolume": number(row[7]),
        "closeTime": int(row[6]),
        "tradeCount": int(row[8]),
        "takerBuyContractVolume": number(row[9]),
        "takerBuyBaseVolume": number(row[9]),
        "takerBuyQuoteVolume": number(row[10]),
    }


def normalize_stream_candle(raw: dict[str, Any]) -> dict[str, Any]:
    base_volume = number(raw.get("v"))
    return {
        "openTime": int(raw.get("t") or 0),
        "open": number(raw.get("o")),
        "high": number(raw.get("h")),
        "low": number(raw.get("l")),
        "close": number(raw.get("c")),
        "contractVolume": base_volume,
        "baseVolume": base_volume,
        "quoteVolume": number(raw.get("q")),
        "closeTime": int(raw.get("T") or 0),
        "tradeCount": int(raw.get("n") or 0),
        "takerBuyContractVolume": number(raw.get("V")),
        "takerBuyBaseVolume": number(raw.get("V")),
        "takerBuyQuoteVolume": number(raw.get("Q")),
        "closed": bool(raw.get("x")),
    }
