import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .config import Settings
from .errors import AppError


class DeltaClient:
    def __init__(self, settings: Settings, api_key: str | None = None, api_secret: str | None = None) -> None:
        self.base_url = settings.delta_production_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        query_string = (
            f"?{urlencode([(key, value) for key, value in (query or {}).items() if value is not None])}"
            if query
            else ""
        )
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "delta-strategy-desk-python/1.0",
        }
        if authenticated:
            if not self.api_key or not self.api_secret:
                raise AppError(401, "Delta connection required", "not_connected")
            timestamp = str(int(time.time()))
            prehash = f"{method}{timestamp}{path}{query_string}{payload}"
            signature = hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()
            headers.update({"api-key": self.api_key, "timestamp": timestamp, "signature": signature})
        try:
            response = await self.client.request(
                method, f"{self.base_url}{path}{query_string}", headers=headers, content=payload or None
            )
        except httpx.HTTPError as exc:
            raise AppError(502, f"Delta Exchange is unreachable: {exc}", "delta_unreachable") from exc
        try:
            data = response.json()
        except ValueError:
            data = {"success": False, "error": {"message": f"Delta returned HTTP {response.status_code}"}}
        if response.is_error or data.get("success") is False:
            delta_error = data.get("error") or {}
            code = str(delta_error.get("code") or f"delta_http_{response.status_code}")
            message = str(delta_error.get("message") or code.replace("_", " "))
            status = response.status_code if 400 <= response.status_code < 500 else 502
            raise AppError(status, message, code)
        return data

    async def profile(self) -> dict[str, Any]:
        return await self.request("GET", "/v2/profile", authenticated=True)

    async def balances(self) -> dict[str, Any]:
        return await self.request("GET", "/v2/wallet/balances", authenticated=True)

    async def open_orders(self, product_ids: list[int] | None = None) -> dict[str, Any]:
        return await self.request(
            "GET",
            "/v2/orders",
            query={
                "product_ids": ",".join(str(product_id) for product_id in product_ids) if product_ids else None,
                "states": "open,pending",
                "page_size": 50,
            },
            authenticated=True,
        )

    async def positions(self) -> dict[str, Any]:
        return await self.request("GET", "/v2/positions/margined", authenticated=True)

    async def position(self, product_id: int) -> dict[str, Any]:
        return await self.request(
            "GET", "/v2/positions", query={"product_id": product_id}, authenticated=True
        )

    async def fills(
        self,
        product_ids: list[int] | None = None,
        start_time: int | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        return await self.request(
            "GET",
            "/v2/fills",
            query={
                "product_ids": ",".join(str(product_id) for product_id in product_ids) if product_ids else None,
                "start_time": start_time,
                "after": after,
                "page_size": 50,
            },
            authenticated=True,
        )

    async def products(self, query: dict[str, Any]) -> dict[str, Any]:
        return await self.request("GET", "/v2/products", query=query)

    async def product(self, symbol: str) -> dict[str, Any]:
        return await self.request("GET", f"/v2/products/{encode_symbol(symbol)}")

    async def ticker(self, symbol: str) -> dict[str, Any]:
        return await self.request("GET", f"/v2/tickers/{encode_symbol(symbol)}")

    async def option_chain(self, underlying: str, expiry: str) -> dict[str, Any]:
        return await self.request(
            "GET",
            "/v2/tickers",
            query={
                "contract_types": "call_options,put_options",
                "underlying_asset_symbols": underlying,
                "expiry_date": expiry,
            },
        )

    async def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/v2/orders", body=order, authenticated=True)

    async def cancel_order(self, order_id: int, product_id: int) -> dict[str, Any]:
        return await self.request(
            "DELETE", "/v2/orders", body={"id": order_id, "product_id": product_id}, authenticated=True
        )


def encode_symbol(symbol: str) -> str:
    return quote(symbol, safe="")
