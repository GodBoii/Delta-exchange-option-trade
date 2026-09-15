"""Exchange events wake reconciliation; only fresh mark events supply valuations."""

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

logger = logging.getLogger(__name__)


class DeltaEvents:
    def __init__(self, api_key: str, api_secret: str, wake: asyncio.Event, public_url: str, private_url: str) -> None:
        self.api_key, self.api_secret = api_key, api_secret
        self.wake = wake
        self.public_url, self.private_url = public_url, private_url
        self.marks: dict[str, tuple[str, float, float]] = {}
        self.symbols: set[str] = set()
        self.connected = {"public": False, "private": False}
        self.tasks: list[asyncio.Task[None]] = []
        self.closed = False

    def start(self) -> None:
        self.tasks = [asyncio.create_task(self.run(kind), name=f"delta-{kind}-events") for kind in self.connected]

    async def close(self) -> None:
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def mark(self, symbol: str, max_age: float) -> str | None:
        if symbol not in self.symbols:
            if len(self.symbols) >= 256:
                return None
            self.symbols.add(symbol)
        value = self.marks.get(symbol)
        if not self.connected["public"] or value is None:
            return None
        price, received, source = value
        return price if time.monotonic() - received <= max_age and -1 <= time.time() - source <= max_age else None

    def receive(self, message: dict[str, Any], kind: str) -> None:
        if kind == "private":
            if message.get("type") in {"orders", "positions", "v2/user_trades", "user_trades"}:
                self.wake.set()
            return
        if message.get("type") != "mark_price":
            return
        symbol = str(message.get("sy") or "").removeprefix("MARK:")
        if symbol not in self.symbols:
            return
        try:
            price = Decimal(str(message.get("p")))
            source = int(message["ts"]) / 1_000_000
        except (InvalidOperation, ValueError, TypeError, KeyError):
            return
        if not price.is_finite() or price <= 0 or source > time.time() + 1:
            return
        previous = self.marks.get(symbol)
        if previous and source <= previous[2]:
            return
        self.marks[symbol] = (str(price), time.monotonic(), source)
        self.wake.set()

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
                                        ]
                                    },
                                }
                            )
                        )
                    self.connected[kind] = True
                    self.wake.set()
                    subscribed: set[str] = set()
                    while not self.closed:
                        if kind == "public" and self.symbols != subscribed:
                            await socket.send(
                                json.dumps(
                                    {
                                        "type": "subscribe",
                                        "payload": {
                                            "channels": [
                                                {
                                                    "name": "mark_price",
                                                    "symbols": [
                                                        f"MARK:{symbol}" for symbol in sorted(self.symbols - subscribed)
                                                    ],
                                                },
                                            ]
                                        },
                                    }
                                )
                            )
                            subscribed = set(self.symbols)
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
                self.wake.set()
            if not self.closed:
                if time.monotonic() - connected_at >= 30 and backoff < 300:
                    backoff = 1
                await asyncio.sleep(backoff + random.uniform(0, min(backoff, 2)))
                backoff = min(backoff * 2, 60)
