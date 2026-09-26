"""Fan PostgreSQL change notifications out to browser revision streams.

Each API process holds one ``LISTEN`` connection, no matter how many browsers are
connected. Every replica receives the same notifications, so a browser may connect
to any of them. Revisions only tell the browser to refetch; they carry no row data.
"""

import asyncio
import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field

import psycopg

logger = logging.getLogger(__name__)
CHANNEL = "trade_changes"
SHARED_OWNER = "global"
FIELDS = {"strategies": "strategies", "analysis_jobs": "automation"}


@dataclass(eq=False)
class Subscription:
    user_id: str
    revisions: dict[str, int] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def take(self) -> dict[str, int]:
        self.changed.clear()
        return dict(self.revisions)


class ChangeFeed:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.subscribers: dict[str, set[Subscription]] = {}
        self.task: asyncio.Task[None] | None = None
        self.connected = False
        self.last_error: str | None = None
        self.counter = time.time_ns() // 1_000_000

    def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="trade-change-feed")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    def subscribe(self, user_id: str) -> Subscription:
        subscription = Subscription(user_id)
        self.subscribers.setdefault(user_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        group = self.subscribers.get(subscription.user_id)
        if group is None:
            return
        group.discard(subscription)
        if not group:
            self.subscribers.pop(subscription.user_id, None)

    def publish(self, table: str, owner: str | None) -> None:
        name = FIELDS.get(table)
        if name is None:
            return
        self.counter += 1
        if owner == SHARED_OWNER or owner is None:
            groups = list(self.subscribers.values())
        else:
            groups = [self.subscribers.get(owner, set())]
        for group in groups:
            for subscription in group:
                subscription.revisions[name] = self.counter
                subscription.changed.set()

    def publish_all(self) -> None:
        """After a reconnect, notifications may have been missed; every browser refetches once."""
        for table in FIELDS:
            self.publish(table, None)

    async def _run(self) -> None:
        delay = 1.0
        first = True
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self.database_url, autocommit=True) as connection:
                    # Read replicas default to read-only transactions; LISTEN needs a writable session.
                    await connection.execute("set default_transaction_read_only = off")
                    await connection.execute(f"listen {CHANNEL}")
                    self.connected = True
                    self.last_error = None
                    delay = 1.0
                    if not first:
                        self.publish_all()
                    first = False
                    async for notice in connection.notifies():
                        try:
                            payload = json.loads(notice.payload)
                        except ValueError:
                            continue
                        if isinstance(payload, dict):
                            self.publish(str(payload.get("table") or ""), payload.get("owner"))
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as error:
                self.connected = False
                self.last_error = type(error).__name__
                logger.warning("Change feed disconnected: %s; retrying in %.0fs", self.last_error, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
