from typing import Any

import httpx

from .client import number
from .config import Settings


class DeltaContextError(RuntimeError):
    pass


class DeltaMarketContextClient:
    """Read-only Delta public ticker client; it has no credentials or order methods."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.delta_public_base_url,
            timeout=httpx.Timeout(10.0, connect=4.0),
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "delta-strategy-desk-market-context/2.0"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def ticker(self) -> dict[str, Any]:
        try:
            response = await self.http.get(f"/v2/tickers/{self.settings.delta_symbol}")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise DeltaContextError("Delta public derivative context is temporarily unavailable") from error
        raw = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            raise DeltaContextError("Delta returned no BTCUSD derivative context")
        return normalize_delta_ticker(raw)


def normalize_delta_ticker(raw: dict[str, Any]) -> dict[str, Any]:
    quotes = raw.get("quotes") if isinstance(raw.get("quotes"), dict) else {}
    timestamp = int(raw.get("timestamp") or 0)
    return {
        "symbol": str(raw.get("symbol") or "BTCUSD"),
        "source": "Delta Exchange",
        "instrumentType": str(raw.get("contract_type") or "perpetual_futures"),
        "lastPrice": number(raw.get("close")),
        "markPrice": number(raw.get("mark_price")),
        "indexPrice": number(raw.get("spot_price")),
        "openInterestBtc": number(raw.get("oi")),
        "openInterestUsd": number(raw.get("oi_value_usd")),
        "volume24hBtc": number(raw.get("volume")),
        "turnover24hUsd": number(raw.get("turnover_usd")),
        "fundingRatePercent": number(raw.get("funding_rate")),
        "bestBid": number(quotes.get("best_bid")),
        "bestAsk": number(quotes.get("best_ask")),
        "bidSizeContracts": number(quotes.get("bid_size")),
        "askSizeContracts": number(quotes.get("ask_size")),
        "exchangeTimestamp": timestamp // 1000 if timestamp > 10_000_000_000_000 else timestamp,
    }
