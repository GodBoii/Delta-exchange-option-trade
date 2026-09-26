"""Authenticated WebSocket that pushes live positions, orders, wallet rows and prices.

All data comes from sockets the engine already holds: the account's private
Delta stream and the shared public mark/index feeds. A connected view costs no
Delta REST calls in steady state. REST is used once to seed wallet rows (the
``margins`` channel has no snapshot), to look up contract metadata for a newly
opened position, and to backfill cashflow fields a few seconds after a fill.

Protocol (JSON text frames):

- client -> server: ``{"type": "auth", "token": "<supabase access token>"}`` first,
  and again whenever the browser refreshes its session.
- server -> client:
  - ``{"type": "status", "private": "live" | "syncing"}``
  - ``{"type": "state", "positions": [...], "orders": [...], "balances": [...], "at": ms}``
  - ``{"type": "prices", "marks": {symbol: price}, "indices": {symbol: price}, "at": ms}``
  - ``{"type": "ping", "at": ms}`` when nothing else was sent for a while

Close codes: 4401 sign-in required, 4403 Delta not connected, 4503 live events
disabled on this replica, 4001 account session replaced (reconnect now).
"""

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Coroutine
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from .auth import current_account
from .config import Settings
from .delta import DeltaClient
from .delta_events import DeltaEvents
from .errors import AppError
from .supabase import SupabaseAdmin

logger = logging.getLogger(__name__)

AUTH_TIMEOUT_SECONDS = 10.0
TICK_SECONDS = 0.2
HEARTBEAT_SECONDS = 20.0
REVERIFY_SECONDS = 300.0
# Delta publishes option marks every two seconds; anything older is not "live".
PRICE_MAX_AGE_SECONDS = 15.0
REST_RETRY_SECONDS = 5.0
MAX_TOKEN_LENGTH = 8192

CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_CONNECTED = 4403
CLOSE_UNAVAILABLE = 4503
CLOSE_SESSION_REPLACED = 4001


class StreamClosed(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


def origin_allowed(origin: str | None, settings: Settings) -> bool:
    """Browsers always send Origin on a WebSocket; a foreign page must not open one."""
    if origin is None:
        return True
    origin = origin.rstrip("/")
    return origin in settings.allowed_origins or bool(re.fullmatch(settings.frontend_origin_regex, origin))


def auth_token(message: Any) -> str | None:
    if not isinstance(message, dict) or message.get("type") != "auth":
        return None
    token = message.get("token")
    return token if isinstance(token, str) and 0 < len(token) <= MAX_TOKEN_LENGTH else None


def price_symbols(positions: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Contract symbols to mark and the index symbols their products settle against."""
    marks: set[str] = set()
    indices: set[str] = set()
    for position in positions:
        symbol = position.get("product_symbol")
        if symbol:
            marks.add(str(symbol))
        product = position.get("product")
        index = product.get("spot_index") if isinstance(product, dict) else None
        if isinstance(index, dict) and index.get("symbol"):
            indices.add(str(index["symbol"]))
    return marks, indices


def now_ms() -> int:
    return int(time.time() * 1000)


class PortfolioStream:
    def __init__(
        self, websocket: WebSocket, db: SupabaseAdmin, engine: Any, events: DeltaEvents, user_id: str, token: str
    ) -> None:
        self.websocket, self.db, self.engine, self.events = websocket, db, engine, events
        self.user_id, self.token = user_id, token
        self.portfolio = events.portfolio
        self.seen_version = -1
        self.sent_version = -1
        self.sent_status: str | None = None
        self.sent_prices: dict[str, dict[str, str]] | None = None
        self.last_sent = time.monotonic()
        self.reverify_at = time.monotonic() + REVERIFY_SECONDS
        self.balances_retry_at = 0.0
        self.product_requests: set[str] = set()
        # Symbols from the last ready state, so prices keep flowing while the
        # private socket reconnects and the view is showing its REST fallback.
        self.mark_symbols: set[str] = set()
        self.index_symbols: set[str] = set()
        self.revoked = False
        self.tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        receiver = asyncio.create_task(self.receive_loop(), name="portfolio-stream-receive")
        sender = asyncio.create_task(self.send_loop(), name="portfolio-stream-send")
        try:
            done, _ = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in (receiver, sender, *self.tasks):
                task.cancel()
            await asyncio.gather(receiver, sender, *self.tasks, return_exceptions=True)

    async def receive_loop(self) -> None:
        while True:
            try:
                message = json.loads(await self.websocket.receive_text())
            except ValueError:
                continue
            token = auth_token(message)
            if token is not None:
                self.token = token

    async def send_loop(self) -> None:
        while True:
            if self.events.closed:
                raise StreamClosed(CLOSE_SESSION_REPLACED, "account_session_replaced")
            if self.revoked:
                raise StreamClosed(CLOSE_UNAUTHORIZED, "session_expired")
            now = time.monotonic()
            self.maintain(now)
            await self.push(now)
            self.seen_version = self.portfolio.version
            await self.portfolio.wait_for_change(self.seen_version, TICK_SECONDS)

    async def send(self, payload: dict[str, Any]) -> None:
        await self.websocket.send_text(json.dumps(payload, separators=(",", ":")))
        self.last_sent = time.monotonic()

    async def push(self, now: float) -> None:
        portfolio = self.portfolio
        live = self.events.connected["private"] and portfolio.ready
        status = "live" if live else "syncing"
        if status != self.sent_status:
            await self.send({"type": "status", "private": status})
            self.sent_status = status
        if live and portfolio.version != self.sent_version:
            snapshot = portfolio.snapshot()
            self.mark_symbols, self.index_symbols = price_symbols(snapshot["positions"])
            await self.send({"type": "state", **snapshot, "at": now_ms()})
            self.sent_version = portfolio.version
        prices = self.prices()
        if prices != self.sent_prices:
            await self.send({"type": "prices", **prices, "at": now_ms()})
            self.sent_prices = prices
        if now - self.last_sent >= HEARTBEAT_SECONDS:
            await self.send({"type": "ping", "at": now_ms()})

    def prices(self) -> dict[str, dict[str, str]]:
        marks = {
            symbol: price
            for symbol in sorted(self.mark_symbols)
            if (price := self.events.watch_mark(symbol, PRICE_MAX_AGE_SECONDS)) is not None
        }
        indices = {
            symbol: price
            for symbol in sorted(self.index_symbols)
            if (price := self.events.spot(symbol, PRICE_MAX_AGE_SECONDS)) is not None
        }
        return {"marks": marks, "indices": indices}

    def maintain(self, now: float) -> None:
        """Starts the few REST lookups the sockets cannot provide; never blocks a push."""
        portfolio = self.portfolio
        if (
            self.events.connected["private"]
            and portfolio.positions_ready
            and not portfolio.balances_ready
            and not portfolio.balances_requested
            and now >= self.balances_retry_at
        ):
            portfolio.balances_requested = True
            self.spawn(self.seed_balances(portfolio.generation))
        for symbol in portfolio.missing_products() - self.product_requests:
            self.product_requests.add(symbol)
            self.spawn(self.load_product(symbol))
        if portfolio.enrich_after is not None and now >= portfolio.enrich_after:
            portfolio.enrich_after = None
            self.spawn(self.enrich_positions())
        if now >= self.reverify_at:
            self.reverify_at = now + REVERIFY_SECONDS
            self.spawn(self.reverify())

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def seed_balances(self, generation: int) -> None:
        try:
            client = await self.engine.client_for_user(self.user_id)
            try:
                rows = (await client.balances()).get("result")
            finally:
                await client.close()
            if not isinstance(rows, list):
                raise AppError(502, "Delta wallet snapshot is unavailable", "balances_unavailable")
            self.portfolio.seed_balances(rows, generation)
        except Exception as error:
            logger.warning("Live portfolio wallet seed failed: %s", type(error).__name__)
            if generation == self.portfolio.generation:
                self.portfolio.balances_requested = False
            self.balances_retry_at = time.monotonic() + REST_RETRY_SECONDS

    async def load_product(self, symbol: str) -> None:
        # Product metadata is public and cached engine-wide, so the shared pool is enough.
        client = DeltaClient(self.engine.settings, http_client=self.engine.exchange_http)
        try:
            self.portfolio.set_product(symbol, await self.engine.product_spec(client, symbol))
        except Exception as error:
            logger.warning("Live portfolio product lookup failed for %s: %s", symbol, type(error).__name__)
            await asyncio.sleep(REST_RETRY_SECONDS)
            self.product_requests.discard(symbol)
        finally:
            await client.close()

    async def enrich_positions(self) -> None:
        try:
            client = await self.engine.client_for_user(self.user_id)
            try:
                rows = (await client.positions()).get("result")
            finally:
                await client.close()
            if isinstance(rows, list):
                self.portfolio.enrich_positions(rows)
        except Exception as error:
            logger.warning("Live portfolio cashflow refresh failed: %s", type(error).__name__)

    async def reverify(self) -> None:
        try:
            user = await self.db.auth_user(self.token)
        except Exception as error:
            # A Supabase outage must not cut every live view; the next check decides.
            logger.warning("Live portfolio session re-check failed: %s", type(error).__name__)
            return
        if not user or str(user.get("id")) != self.user_id:
            self.revoked = True


async def authenticate(websocket: WebSocket) -> str:
    try:
        message = json.loads(await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_SECONDS))
    except (TimeoutError, ValueError) as error:
        raise StreamClosed(CLOSE_UNAUTHORIZED, "auth_required") from error
    token = auth_token(message)
    if token is None:
        raise StreamClosed(CLOSE_UNAUTHORIZED, "auth_required")
    return token


async def serve_portfolio(websocket: WebSocket, db: SupabaseAdmin, engine: Any, settings: Settings) -> None:
    if not origin_allowed(websocket.headers.get("origin"), settings):
        await websocket.close(code=1008, reason="origin_not_allowed")
        return
    await websocket.accept()
    try:
        token = await authenticate(websocket)
        user = await db.auth_user(token)
        if not user:
            raise StreamClosed(CLOSE_UNAUTHORIZED, "not_authenticated")
        try:
            await current_account(db, user, required=True)
        except AppError as error:
            raise StreamClosed(CLOSE_NOT_CONNECTED, error.code) from error
        if not getattr(engine.settings, "delta_events_enabled", False):
            raise StreamClosed(CLOSE_UNAVAILABLE, "live_events_disabled")
        user_id = str(user["id"])
        try:
            async with engine.watch_account(user_id) as events:
                await PortfolioStream(websocket, db, engine, events, user_id, token).run()
        except AppError as error:
            # Only session setup raises AppError; background lookups handle their own.
            code = CLOSE_NOT_CONNECTED if error.status == 401 else CLOSE_UNAVAILABLE
            raise StreamClosed(code, error.code) from error
    except WebSocketDisconnect:
        return
    except StreamClosed as closed:
        with contextlib.suppress(Exception):
            await websocket.close(code=closed.code, reason=closed.reason)
    except Exception:
        logger.exception("Live portfolio stream failed")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="internal_error")
