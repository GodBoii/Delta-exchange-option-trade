"""Exchange events wake reconciliation; only fresh mark events supply valuations.

Marks requested through ``mark`` feed risk checks and wake the engine. Marks and
index prices requested through ``watch_mark`` and ``spot`` only feed the live
portfolio view, so display traffic never adds scheduler cycles.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from websockets.asyncio.client import connect

from .portfolio_state import AccountPortfolio

logger = logging.getLogger(__name__)
MAX_PUBLIC_SYMBOLS = 256
PriceSample = tuple[str, float, float]


def fresh_price(value: PriceSample | None, max_age: float) -> str | None:
    if value is None:
        return None
    price, received, source = value
    return price if time.monotonic() - received <= max_age and -1 <= time.time() - source <= max_age else None


def parse_price(message: dict[str, Any]) -> tuple[str, float] | None:
    try:
        price = Decimal(str(message.get("p")))
        source = int(message["ts"]) / 1_000_000
    except (InvalidOperation, ValueError, TypeError, KeyError):
        return None
    if not price.is_finite() or price <= 0 or source > time.time() + 1:
        return None
    return str(price), source


class DeltaEvents:
    def __init__(
        self, api_key: str, api_secret: str, wake: asyncio.Event, public_url: str, private_url: str,
        shared_marks: "PublicMarkFeeds | None" = None,
    ) -> None:
        self.api_key, self.api_secret = api_key, api_secret
        self.wake = wake
        self.public_url, self.private_url = public_url, private_url
        self.shared_marks = shared_marks
        self.marks: dict[str, PriceSample] = {}
        self.spots: dict[str, PriceSample] = {}
        # Risk marks wake the engine; watched marks and spot indices do not.
        self.symbols: set[str] = set()
        self.watched: set[str] = set()
        self.spot_symbols: set[str] = set()
        self.portfolio = AccountPortfolio()
        self.connected = {"public": False, "private": False}
        self.tasks: list[asyncio.Task[None]] = []
        self.closed = False

    def start(self, kinds: tuple[str, ...] = ("public", "private")) -> None:
        self.tasks = [asyncio.create_task(self.run(kind), name=f"delta-{kind}-events") for kind in kinds]

    async def close(self) -> None:
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def load(self) -> int:
        return len(self.symbols | self.watched) + len(self.spot_symbols)

    def mark(self, symbol: str, max_age: float) -> str | None:
        if self.shared_marks is not None:
            return self.shared_marks.mark(symbol, max_age)
        if symbol not in self.symbols:
            if symbol not in self.watched and self.load() >= MAX_PUBLIC_SYMBOLS:
                return None
            self.symbols.add(symbol)
        return fresh_price(self.marks.get(symbol), max_age) if self.connected["public"] else None

    def watch_mark(self, symbol: str, max_age: float) -> str | None:
        """Display-only mark: subscribed like ``mark`` but never wakes the engine."""
        if self.shared_marks is not None:
            return self.shared_marks.watch_mark(symbol, max_age)
        if symbol not in self.symbols and symbol not in self.watched:
            if self.load() >= MAX_PUBLIC_SYMBOLS:
                return None
            self.watched.add(symbol)
        return fresh_price(self.marks.get(symbol), max_age) if self.connected["public"] else None

    def spot(self, symbol: str, max_age: float) -> str | None:
        """Underlying index price such as ``.DEXBTUSD``; display-only."""
        if self.shared_marks is not None:
            return self.shared_marks.spot(symbol, max_age)
        if symbol not in self.spot_symbols:
            if self.load() >= MAX_PUBLIC_SYMBOLS:
                return None
            self.spot_symbols.add(symbol)
        return fresh_price(self.spots.get(symbol), max_age) if self.connected["public"] else None

    def receive(self, message: dict[str, Any], kind: str) -> None:
        if kind == "private":
            self.portfolio.apply(message, time.monotonic())
            if message.get("type") in {"orders", "positions", "v2/user_trades", "user_trades"}:
                self.wake.set()
            return
        message_type = message.get("type")
        if message_type == "spot_price":
            symbol = str(message.get("sy") or "")
            if symbol in self.spot_symbols:
                self._store(self.spots, symbol, message)
            return
        if message_type != "mark_price":
            return
        symbol = str(message.get("sy") or "").removeprefix("MARK:")
        if symbol not in self.symbols and symbol not in self.watched:
            return
        if self._store(self.marks, symbol, message) and symbol in self.symbols:
            self.wake.set()

    @staticmethod
    def _store(prices: dict[str, PriceSample], symbol: str, message: dict[str, Any]) -> bool:
        parsed = parse_price(message)
        if parsed is None:
            return False
        price, source = parsed
        previous = prices.get(symbol)
        if previous and source <= previous[2]:
            return False
        prices[symbol] = (price, time.monotonic(), source)
        return True

    async def run(self, kind: str) -> None:
        backoff = 1.0
        while not self.closed:
            connected_at = time.monotonic()
            try:
                async with connect(
                    self.public_url if kind == "public" else self.private_url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                    max_queue=256,
                ) as socket:
                    if kind == "private":
                        timestamp = int(time.time())
                        signature = hmac.new(
                            self.api_secret.encode(), f"GET{timestamp}/live".encode(), hashlib.sha256
                        ).hexdigest()
                        await socket.send(
                            json.dumps(
                                {
                                    "type": "key-auth",
                                    "payload": {
                                        "api-key": self.api_key,
                                        "timestamp": timestamp,
                                        "signature": signature,
                                    },
                                }
                            )
                        )
                        authenticated = False
                        async with asyncio.timeout(10):
                            while not authenticated:
                                response = json.loads(await socket.recv())
                                if response.get("type") == "key-auth":
                                    if response.get("success") is not True:
                                        raise RuntimeError("Private stream authentication rejected")
                                    authenticated = True
                        await socket.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "payload": {
                                        "channels": [
                                            {"name": "orders", "symbols": ["all"]},
                                            {"name": "positions", "symbols": ["all"]},
                                            {"name": "v2/user_trades", "symbols": ["all"]},
                                            # Wallet rows for the live portfolio view.
                                            {"name": "margins"},
                                        ]
                                    },
                                }
                            )
                        )
                    self.connected[kind] = True
                    self.wake.set()
                    subscribed: set[str] = set()
                    subscribed_spots: set[str] = set()
                    while not self.closed:
                        if kind == "public":
                            marks = sorted((self.symbols | self.watched) - subscribed)
                            spots = sorted(self.spot_symbols - subscribed_spots)
                            channels = [
                                *([{"name": "mark_price", "symbols": [f"MARK:{symbol}" for symbol in marks]}]
                                  if marks else []),
                                *([{"name": "spot_price", "symbols": spots}] if spots else []),
                            ]
                            if channels:
                                await socket.send(json.dumps({"type": "subscribe", "payload": {"channels": channels}}))
                                subscribed.update(marks)
                                subscribed_spots.update(spots)
                        try:
                            message = await asyncio.wait_for(socket.recv(), timeout=1)
                        except TimeoutError:
                            continue
                        value = json.loads(message)
                        if isinstance(value, dict):
                            self.receive(value, kind)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Delta %s stream disconnected: %s", kind, type(error).__name__)
                if getattr(getattr(error, "response", None), "status_code", None) == 429:
                    backoff = 300
            finally:
                self.connected[kind] = False
                if kind == "public":
                    self.marks.clear()
                    self.spots.clear()
                else:
                    self.portfolio.reset()
                self.wake.set()
            if not self.closed:
                if time.monotonic() - connected_at >= 30 and backoff < 300:
                    backoff = 1
                await asyncio.sleep(backoff + random.uniform(0, min(backoff, 2)))
                backoff = min(backoff * 2, 60)


class PublicMarkFeeds:
    """One public mark socket per bounded group of option symbols, shared by accounts."""

    def __init__(self, wake: asyncio.Event, public_url: str, private_url: str) -> None:
        self.wake = wake
        self.public_url, self.private_url = public_url, private_url
        self.feeds: list[DeltaEvents] = []
        self.by_symbol: dict[str, DeltaEvents] = {}
        self.last_used: dict[DeltaEvents, float] = {}
        self.closed = False

    def _feed(self, key: str) -> DeltaEvents:
        feed = self.by_symbol.get(key)
        if feed is None:
            feed = next((item for item in self.feeds if item.load() < MAX_PUBLIC_SYMBOLS), None)
            if feed is None:
                feed = DeltaEvents("", "", self.wake, self.public_url, self.private_url)
                self.feeds.append(feed)
                feed.start(("public",))
            self.by_symbol[key] = feed
        self.last_used[feed] = time.monotonic()
        return feed

    def mark(self, symbol: str, max_age: float) -> str | None:
        if self.closed:
            return None
        return self._feed(symbol).mark(symbol, max_age)

    def watch_mark(self, symbol: str, max_age: float) -> str | None:
        if self.closed:
            return None
        return self._feed(symbol).watch_mark(symbol, max_age)

    def spot(self, symbol: str, max_age: float) -> str | None:
        if self.closed:
            return None
        # Index symbols live in their own key space so they can never alias a contract.
        return self._feed(f"spot:{symbol}").spot(symbol, max_age)

    async def prune(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        idle = {feed for feed, used in self.last_used.items() if current - used >= 900}
        if not idle:
            return
        self.feeds = [feed for feed in self.feeds if feed not in idle]
        self.by_symbol = {symbol: feed for symbol, feed in self.by_symbol.items() if feed not in idle}
        for feed in idle:
            self.last_used.pop(feed, None)
        await asyncio.gather(*(feed.close() for feed in idle))

    async def close(self) -> None:
        self.closed = True
        await asyncio.gather(*(feed.close() for feed in self.feeds))
