import asyncio
import time
from typing import Any

import httpx

from .client import number
from .config import Settings


class DeltaContextError(RuntimeError):
    pass


class DeltaMarketContextClient:
    """Read-only Delta public market client; it never receives credentials or places orders."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.contract_value = 0.001
        self.http = httpx.AsyncClient(
            base_url=settings.delta_public_base_url,
            timeout=httpx.Timeout(10.0, connect=4.0),
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "delta-strategy-desk-market-context/3.0"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def ticker(self) -> dict[str, Any]:
        raw = await self._result(f"/v2/tickers/{self.settings.delta_symbol}")
        if not isinstance(raw, dict):
            raise DeltaContextError("Delta returned no BTCUSD derivative context")
        return normalize_delta_ticker(raw)

    async def snapshot(self, include_slow_data: bool = False) -> dict[str, Any]:
        """Fetch live execution-market data and periodically refreshed contract/history data."""
        ticker = await self.ticker()
        book_result, trades_result = await asyncio.gather(
            self._result(f"/v2/l2orderbook/{self.settings.delta_symbol}"),
            self._result(f"/v2/trades/{self.settings.delta_symbol}"),
            return_exceptions=True,
        )
        snapshot = dict(ticker)
        if isinstance(book_result, dict):
            snapshot["orderBook"] = normalize_delta_order_book(book_result, self.contract_value)
        if isinstance(trades_result, list):
            snapshot["recentTrades"] = normalize_delta_trades(trades_result, self.contract_value)

        if include_slow_data:
            now = int(time.time())
            start = now - 48 * 60 * 60
            product_result, oi_result, funding_result, mark_result = await asyncio.gather(
                self._result(f"/v2/products/{self.settings.delta_symbol}"),
                self._result("/v2/history/candles", {"resolution": "1h", "symbol": f"OI:{self.settings.delta_symbol}", "start": start, "end": now}),
                self._result("/v2/history/candles", {"resolution": "1h", "symbol": f"FUNDING:{self.settings.delta_symbol}", "start": start, "end": now}),
                self._result("/v2/history/candles", {"resolution": "1h", "symbol": f"MARK:{self.settings.delta_symbol}", "start": start, "end": now}),
                return_exceptions=True,
            )
            if isinstance(product_result, dict):
                product = normalize_delta_product(product_result)
                self.contract_value = product["contractValueBtc"] or self.contract_value
                snapshot["product"] = product
            if isinstance(oi_result, list):
                snapshot["openInterestHistory"] = normalize_delta_history(oi_result)
            if isinstance(funding_result, list):
                snapshot["fundingHistory"] = normalize_delta_history(funding_result)
            if isinstance(mark_result, list):
                snapshot["markPriceHistory"] = normalize_delta_history(mark_result)
        return snapshot

    async def _result(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self.http.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise DeltaContextError("Delta public market data is temporarily unavailable") from error
        if not isinstance(payload, dict) or payload.get("success") is False or "result" not in payload:
            raise DeltaContextError("Delta returned an invalid public market response")
        return payload["result"]


def normalize_delta_ticker(raw: dict[str, Any]) -> dict[str, Any]:
    quotes = raw.get("quotes") if isinstance(raw.get("quotes"), dict) else {}
    price_band = raw.get("price_band") if isinstance(raw.get("price_band"), dict) else {}
    timestamp = int(raw.get("timestamp") or 0)
    return {
        "symbol": str(raw.get("symbol") or "BTCUSD"),
        "source": "Delta Exchange",
        "instrumentType": str(raw.get("contract_type") or "perpetual_futures"),
        "tradingStatus": str(raw.get("product_trading_status") or "unknown"),
        "lastPrice": number(raw.get("close")),
        "open24h": number(raw.get("open")),
        "high24h": number(raw.get("high")),
        "low24h": number(raw.get("low")),
        "lastPriceChange24hPercent": number(raw.get("ltp_change_24h")),
        "markPrice": number(raw.get("mark_price")),
        "markHigh24h": number(raw.get("mark_high_24h")),
        "markLow24h": number(raw.get("mark_low_24h")),
        "markChange24hPercent": number(raw.get("mark_change_24h")),
        "markBasisPercent": number(raw.get("mark_basis")) * 100,
        "indexPrice": number(raw.get("spot_price")),
        "openInterestBtc": number(raw.get("oi")),
        "openInterestContracts": number(raw.get("oi_contracts")),
        "openInterestUsd": number(raw.get("oi_value_usd")),
        "openInterestChange6hUsd": number(raw.get("oi_change_usd_6h")),
        "volume24hBtc": number(raw.get("volume")),
        "volume24hContracts": number(raw.get("size")),
        "turnover24hUsd": number(raw.get("turnover_usd")),
        "fundingRatePercent": number(raw.get("funding_rate")),
        "leverage": number(raw.get("leverage")),
        "tickSize": number(raw.get("tick_size")),
        "contractValueBtc": number(raw.get("contract_value")),
        "priceBandLower": number(price_band.get("lower_limit")),
        "priceBandUpper": number(price_band.get("upper_limit")),
        "bestBid": number(quotes.get("best_bid")),
        "bestAsk": number(quotes.get("best_ask")),
        "bidSizeContracts": number(quotes.get("bid_size")),
        "askSizeContracts": number(quotes.get("ask_size")),
        "exchangeTimestamp": timestamp // 1000 if timestamp > 10_000_000_000_000 else timestamp,
    }


def normalize_delta_product(raw: dict[str, Any]) -> dict[str, Any]:
    underlying = raw.get("underlying_asset") if isinstance(raw.get("underlying_asset"), dict) else {}
    quoting = raw.get("quoting_asset") if isinstance(raw.get("quoting_asset"), dict) else {}
    settling = raw.get("settling_asset") if isinstance(raw.get("settling_asset"), dict) else {}
    spot_index = raw.get("spot_index") if isinstance(raw.get("spot_index"), dict) else {}
    specs = raw.get("product_specs") if isinstance(raw.get("product_specs"), dict) else {}
    return {
        "productId": int(raw.get("id") or 0),
        "symbol": str(raw.get("symbol") or "BTCUSD"),
        "description": str(raw.get("description") or raw.get("short_description") or "Bitcoin perpetual"),
        "state": str(raw.get("state") or "unknown"),
        "tradingStatus": str(raw.get("trading_status") or "unknown"),
        "contractType": str(raw.get("contract_type") or "perpetual_futures"),
        "underlyingAsset": str(underlying.get("symbol") or "BTC"),
        "quotingAsset": str(quoting.get("symbol") or "USD"),
        "settlingAsset": str(settling.get("symbol") or "USD"),
        "indexSymbol": str(spot_index.get("symbol") or ""),
        "indexDescription": str(spot_index.get("description") or ""),
        "contractValueBtc": number(raw.get("contract_value")),
        "tickSize": number(raw.get("tick_size")),
        "defaultLeverage": number(raw.get("default_leverage")),
        "positionSizeLimitContracts": number(raw.get("position_size_limit")),
        "initialMarginPercent": number(raw.get("initial_margin")),
        "maintenanceMarginPercent": number(raw.get("maintenance_margin")),
        "makerFeePercent": number(raw.get("maker_commission_rate")) * 100,
        "takerFeePercent": number(raw.get("taker_commission_rate")) * 100,
        "fundingIntervalHours": number(specs.get("rate_exchange_interval")) / 3600,
        "launchTime": raw.get("launch_time"),
    }


def normalize_delta_order_book(raw: dict[str, Any], contract_value: float) -> dict[str, Any]:
    def levels(side: str) -> list[list[float]]:
        values = raw.get(side) if isinstance(raw.get(side), list) else []
        return [
            [number(level.get("price")), number(level.get("size")), number(level.get("depth")), number(level.get("size")) * contract_value]
            for level in values[:15]
            if isinstance(level, dict)
        ]

    return {"symbol": str(raw.get("symbol") or "BTCUSD"), "bids": levels("buy"), "asks": levels("sell")}


def normalize_delta_trades(raw: list[Any], contract_value: float) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for index, trade in enumerate(raw[:30]):
        if not isinstance(trade, dict):
            continue
        timestamp = int(trade.get("timestamp") or 0)
        size = number(trade.get("size"))
        trade_price = number(trade.get("price"))
        side = "buy" if trade.get("buyer_role") == "taker" else "sell"
        trades.append({
            "id": f"{timestamp}-{index}",
            "price": trade_price,
            "sizeContracts": size,
            "sizeBtc": size * contract_value,
            "notionalUsd": size * contract_value * trade_price,
            "side": side,
            "time": timestamp // 1000 if timestamp > 10_000_000_000_000 else timestamp,
        })
    return trades


def normalize_delta_history(raw: list[Any]) -> list[dict[str, float | int]]:
    points = [
        {"time": int(point.get("time") or 0) * 1000, "open": number(point.get("open")), "high": number(point.get("high")), "low": number(point.get("low")), "close": number(point.get("close"))}
        for point in raw
        if isinstance(point, dict)
    ]
    return sorted(points, key=lambda point: point["time"])
