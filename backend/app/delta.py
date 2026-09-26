import asyncio
import hashlib
import hmac
import json
import time
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .config import Settings
from .delta_events import DeltaEvents
from .errors import AppError, DeltaOrderRejected
from .order_journal import OrderJournal


class RequestBudget:
    def __init__(self) -> None:
        self.reads = asyncio.Semaphore(6)
        self.orders = asyncio.Semaphore(2)
        self.blocked_until = 0.0
        self.used: deque[tuple[float, int]] = deque()

    def charge(self, weight: int, priority: bool, mutation: bool = False) -> None:
        now = time.monotonic()
        while self.used and self.used[0][0] < now - 300:
            self.used.popleft()
        if now < self.blocked_until:
            raise AppError(429, "Exchange rate limit reset is pending", "delta_rate_limited")
        if sum(item[1] for item in self.used) + weight > (19500 if mutation else 19000 if priority else 16000):
            raise AppError(429, "Account request budget reserved for recovery", "delta_rate_limited")
        self.used.append((now, weight))


class DeltaClient:
    def __init__(
        self,
        settings: Settings,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        release: Callable[[], None] | None = None,
        budget: RequestBudget | None = None,
        events: DeltaEvents | None = None,
    ) -> None:
        self.base_url = settings.delta_production_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0))
        self.release = release
        self.owns_http = http_client is None
        self.budget = budget or RequestBudget()
        self.events = events
        self.mark_max_age = getattr(settings, "delta_mark_max_age_seconds", 5.0)
        self.order_journal: OrderJournal | None = None

    async def close(self) -> None:
        if self.release is not None:
            release, self.release = self.release, None
            release()
        elif self.owns_http:
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
        priority = (
            method in {"POST", "DELETE"}
            or path in {"/v2/positions", "/v2/positions/margined", "/v2/orders", "/v2/fills"}
            or "/client_order_id/" in path
        )
        weight = 10 if path == "/v2/fills" else 5 if method != "GET" else 3
        try:
            async with self.budget.orders if method != "GET" else self.budget.reads:
                self.budget.charge(weight, priority, method != "GET")
                response = await self.client.request(
                    method, f"{self.base_url}{path}{query_string}", headers=headers, content=payload or None
                )
        except httpx.HTTPError as exc:
            raise AppError(502, f"Delta Exchange is unreachable: {exc}", "delta_unreachable") from exc
        if response.status_code == 429:
            try:
                delay = float(response.headers.get("X-RATE-LIMIT-RESET", "1000")) / 1000
            except ValueError:
                delay = 1.0
            self.budget.blocked_until = time.monotonic() + max(1, min(delay, 300))
        try:
            data = response.json()
        except ValueError:
            data = {"success": False, "error": {"message": f"Delta returned HTTP {response.status_code}"}}
        if not isinstance(data, dict):
            raise AppError(502, "Delta returned an invalid response", "invalid_delta_response")
        if response.is_error or data.get("success") is False:
            delta_error = data.get("error")
            delta_error = delta_error if isinstance(delta_error, dict) else {}
            code = str(delta_error.get("code") or f"delta_http_{response.status_code}")
            message = str(delta_error.get("message") or code.replace("_", " "))
            status = response.status_code if 400 <= response.status_code < 500 else 502
            error_type = (
                DeltaOrderRejected
                if method == "POST"
                and path == "/v2/orders"
                and 400 <= response.status_code < 500
                and data.get("success") is False
                and delta_error.get("code")
                and "client_order" not in code.lower()
                else AppError
            )
            raise error_type(status, message, code)
        return data

    async def order_by_client_id(self, client_order_id: str) -> dict[str, Any]:
        return await self.request(
            "GET", f"/v2/orders/client_order_id/{quote(client_order_id, safe='')}", authenticated=True
        )

    async def profile(self) -> dict[str, Any]:
        return await self.request("GET", "/v2/profile", authenticated=True)

    async def balances(self) -> dict[str, Any]:
        return await self.request("GET", "/v2/wallet/balances", authenticated=True)

    async def open_orders(
        self,
        product_ids: list[int] | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        return await self.request(
            "GET",
            "/v2/orders",
            query={
                "product_ids": ",".join(str(product_id) for product_id in product_ids) if product_ids else None,
                "states": "open,pending",
                "after": after,
                "page_size": 50,
            },
            authenticated=True,
        )

    async def positions(self) -> dict[str, Any]:
        return await self.request("GET", "/v2/positions/margined", authenticated=True)

    async def position(self, product_id: int) -> dict[str, Any]:
        return await self.request("GET", "/v2/positions", query={"product_id": product_id}, authenticated=True)

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

    async def order_leverage(self, product_id: int) -> dict[str, Any]:
        return await self.request("GET", f"/v2/products/{product_id}/orders/leverage", authenticated=True)

    async def ticker(self, symbol: str) -> dict[str, Any]:
        return await self.request("GET", f"/v2/tickers/{encode_symbol(symbol)}")

    async def risk_ticker(self, symbol: str) -> dict[str, Any]:
        mark = self.events.mark(symbol, self.mark_max_age) if self.events is not None else None
        if mark is not None:
            return {"result": {"mark_price": mark}}
        return await self.ticker(symbol)

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

    async def place_order(self, order: dict[str, Any], *, context: dict[str, str] | None = None) -> dict[str, Any]:
        if self.order_journal is not None:
            return await self.order_journal.submit(
                order,
                lambda: self.request("POST", "/v2/orders", body=order, authenticated=True),
                self.order_by_client_id,
                context=context,
            )
        return await self.request("POST", "/v2/orders", body=order, authenticated=True)

    async def cancel_order(self, order_id: int, product_id: int) -> dict[str, Any]:
        return await self.request(
            "DELETE", "/v2/orders", body={"id": order_id, "product_id": product_id}, authenticated=True
        )


def encode_symbol(symbol: str) -> str:
    return quote(symbol, safe="")
