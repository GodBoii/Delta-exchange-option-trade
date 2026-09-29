"""Public option/futures collection inside the existing per-asset market service."""

import asyncio
import logging
import time
from typing import Any

import httpx

from .config import Settings
from .evidence import finite, normalize_option, option_overview, timestamp_ms
from .evidence_store import EvidenceStore

logger = logging.getLogger(__name__)


class PublicEvidence:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.asset = settings.base_asset
        self.store = EvidenceStore(settings.market_history_path, self.asset)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(10, connect=4), transport=transport)
        self.limit = asyncio.Semaphore(4)
        self.blocked_until: dict[str, float] = {}
        self.products: dict[str, dict] = {}
        self.metadata_at = 0.0
        self.options: list[dict] = []
        self.options_at = 0
        self.futures: dict[str, Any] = {}
        self.overview: list[dict] = []
        self.funding_info: dict[str, float] = {}
        self.funding_info_at = 0.0
        self._options_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    async def get(self, base: str, path: str, params: dict | None = None) -> Any:
        if time.monotonic() < self.blocked_until.get(base, 0):
            raise ValueError("Public source cooldown")
        async with self.limit:
            response = await self.http.get(base + path, params=params)
            if response.status_code in {418, 429}:
                retry = finite(response.headers.get("Retry-After"))
                self.blocked_until[base] = time.monotonic() + max(60, min(retry or 60, 3600))
            response.raise_for_status()
            payload = response.json()
        if base == self.settings.delta_public_base_url and (
            not isinstance(payload, dict) or payload.get("success") is not True
        ):
            raise ValueError("Invalid Delta response")
        return payload

    async def refresh_products(self) -> None:
        base = self.settings.delta_public_base_url
        products = {}
        cursor = None
        seen = set()
        while True:
            payload = await self.get(base, "/v2/products", {
                "contract_types": "call_options,put_options", "states": "live", "page_size": 100,
                **({"after": cursor} if cursor else {}),
            })
            if not isinstance(payload.get("result"), list):
                raise ValueError("Invalid product catalogue")
            for row in payload["result"]:
                if isinstance(row, dict) and f"-{self.asset}-" in str(row.get("symbol")):
                    products[row["symbol"]] = row
            cursor = (payload.get("meta") or {}).get("after")
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("Product pagination did not advance")
            seen.add(cursor)
        self.products = products
        self.metadata_at = time.monotonic()

    async def refresh_options(self) -> None:
        async with self._options_lock:
            if not self.products or time.monotonic() - self.metadata_at > 300:
                await self.refresh_products()
            payload = await self.get(self.settings.delta_public_base_url, "/v2/tickers", {
                "contract_types": "call_options,put_options", "underlying_asset_symbols": self.asset,
            })
            if not isinstance(payload.get("result"), list):
                raise ValueError("Invalid option chain")
            now = int(time.time() * 1000)
            rows = []
            for raw in payload["result"]:
                if not isinstance(raw, dict):
                    continue
                product = self.products.get(raw.get("symbol"))
                if not product:
                    continue
                try:
                    rows.append(normalize_option(raw, product, self.asset, now))
                except ValueError:
                    continue
            self.options, self.options_at = rows, now
            self.overview = await asyncio.to_thread(self.summarize_options, now)

    def summarize_options(self, now: int, extra: set[int] | None = None) -> list[dict]:
        options = self.options
        rows = option_overview(options, now, extra)
        for row in rows:
            changes = []
            for symbol in row.pop("atmSymbols"):
                history = self.store.read("option_observations", now - 80 * 60_000, now - 50 * 60_000, symbol)
                if history and history[-1].get("impliedVolatility") is not None:
                    current = next(o for o in options if o["symbol"] == symbol)
                    if current.get("impliedVolatility") is not None:
                        changes.append((current["impliedVolatility"] - history[-1]["impliedVolatility"]) * 100)
            row["sameAtmContractsIvChange1hPoints"] = sum(changes) / len(changes) if len(changes) == 2 else None
        return rows

    async def refresh_futures(self) -> None:
        base, symbol = self.settings.binance_futures_base_url, self.settings.binance_symbol
        if not self.funding_info or time.monotonic() - self.funding_info_at > 300:
            info = await self.get(base, "/fapi/v1/fundingInfo")
            if not isinstance(info, list):
                raise ValueError("Invalid funding intervals")
            self.funding_info = {r["symbol"]: finite(r.get("fundingIntervalHours"), positive=True)
                                 for r in info if isinstance(r, dict) and "symbol" in r}
            self.funding_info_at = time.monotonic()
        oi, mark = await asyncio.gather(
            self.get(base, "/fapi/v1/openInterest", {"symbol": symbol}),
            self.get(base, "/fapi/v1/premiumIndex", {"symbol": symbol}),
        )
        now = int(time.time() * 1000)
        if oi.get("symbol") != symbol or mark.get("symbol") != symbol:
            raise ValueError("Futures asset mismatch")
        observed = timestamp_ms(mark.get("time"), now)
        oi_at = timestamp_ms(oi.get("time"), now)
        index, price = finite(mark.get("indexPrice"), positive=True), finite(mark.get("markPrice"), positive=True)
        interest, rate = finite(oi.get("openInterest")), finite(mark.get("lastFundingRate"))
        if (observed is None or oi_at is None or index is None or price is None or interest is None
                or interest < 0 or not 0 <= now - min(observed, oi_at) <= 120_000):
            raise ValueError("Invalid futures values")
        self.futures = {
            "symbol": symbol, "observedAt": min(observed, oi_at), "receivedAt": now,
            "oiBase": interest, "oiQuote": interest * price, "quoteUnit": "USDT",
            "basisPercent": (price / index - 1) * 100,
            "fundingPercent": rate * 100 if rate is not None else None,
            # fundingInfo lists interval exceptions; Binance's documented default is eight hours.
            "fundingIntervalHours": self.funding_info.get(symbol, 8),
            "nextFundingAt": finite(mark.get("nextFundingTime")),
        }

    async def selected(self, symbols: list[str]) -> list[dict]:
        if len(symbols) > 16 or len(set(symbols)) != len(symbols):
            raise ValueError("Invalid selected contract count")
        if not self.products or time.monotonic() - self.metadata_at > 300:
            await self.refresh_products()

        async def load(symbol: str) -> dict:
            if f"-{self.asset}-" not in symbol or symbol not in self.products:
                raise ValueError("Contract does not match asset or catalogue")
            base = self.settings.delta_public_base_url
            ticker, book = await asyncio.gather(
                self.get(base, f"/v2/tickers/{symbol}"), self.get(base, f"/v2/l2orderbook/{symbol}", {"depth": 100}),
            )
            raw_book = book["result"]
            if raw_book.get("symbol") != symbol:
                raise ValueError("Depth contract mismatch")
            result = normalize_option(ticker["result"], self.products[symbol], self.asset, int(time.time() * 1000))
            if not 0 <= result["receivedAt"] - result["observedAt"] <= 30_000:
                raise ValueError("Selected option quote is stale")
            result["depth"] = raw_book
            return result

        tasks = [asyncio.create_task(load(symbol)) for symbol in symbols]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    def futures_summary(self, delta: dict, now: int) -> dict:
        candidates = {"Binance": self.futures}
        if delta.get("receivedAt") and now - delta["receivedAt"] <= 30_000:
            metrics = delta.get("agentMetrics") or {}
            candidates["Delta"] = {
                "symbol": self.settings.delta_symbol, "observedAt": delta.get("exchangeTimestamp"),
                "receivedAt": delta["receivedAt"], "quoteUnit": "USD",
                "oiBase": metrics.get("oiBase"), "oiQuote": metrics.get("oiQuote"),
                "basisPercent": metrics.get("basisPercent"), "fundingPercent": metrics.get("fundingPercent"),
                "fundingIntervalHours": finite((delta.get("product") or {}).get("fundingIntervalHours"), positive=True),
            }
        result = {}
        for venue, row in candidates.items():
            if not row or not 0 <= now - row.get("receivedAt", 0) <= 120_000:
                continue
            if row.get("observedAt") and not 0 <= now - row["observedAt"] <= 120_000:
                continue
            row = dict(row)
            old = self.store.read("futures_observations", now - 80 * 60_000, now - 50 * 60_000, venue)
            previous = old[-1].get("oiBase") if old else None
            current = row.get("oiBase")
            row["oiChange1hPercent"] = (current / previous - 1) * 100 if previous and current is not None else None
            result[venue] = row
        return result

    def retained_options(self, now: int) -> list[dict]:
        watched = self.store.watched(now)
        return [row for row in self.options if (row["expiryMs"] <= now + 30 * 86_400_000 or
                row["expiryMs"] in watched) and 0 <= now - row["observedAt"] <= 90_000]
