"""Web Push for the installed app: short trade alerts on the phone's lock screen.

Browsers hand out a subscription (an HTTPS endpoint plus two keys). The writer
stores it in ``trade.push_subscriptions`` and posts encrypted payloads to it with
VAPID. The service worker in ``public/sw.js`` turns each payload into a system
notification. Sending never blocks trading: callers use ``spawn`` and every
failure ends in a log line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, assert_never

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field
from pywebpush import WebPushException, webpush

logger = logging.getLogger(__name__)

StrategyEvent = Literal["activated", "entry_failed", "entry_rejected", "closed", "exit_failed"]
# Delta option symbols look like C-BTC-84000-280926.
OPTION_SYMBOL = re.compile(r"^([CP])-[A-Z]+-(\d+(?:\.\d+)?)-")
EXIT_REASONS = {
    "stop_loss": "Stop loss hit",
    "take_profit": "Target hit",
    "partial_entry_timeout": "Partial entry closed",
    "external_leg_exit": "Leg closed on Delta",
}
# Push services drop subscriptions that are gone; these codes mean "delete it".
GONE_STATUSES = frozenset({404, 410})
PUSH_TTL_SECONDS = 3_600
DETAIL_LIMIT = 120


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    tag: str
    url: str = "/"
    urgent: bool = False

    def payload(self) -> str:
        return json.dumps({"title": self.title, "body": self.body, "tag": self.tag, "url": self.url})


class SubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="ignore")
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscriptionIn(BaseModel):
    """The object ``PushSubscription.toJSON()`` returns in the browser."""

    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(min_length=12, max_length=2048, pattern=r"^https://")
    keys: SubscriptionKeys


class PushSubscriptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=12, max_length=2048)


def leg_label(side: object, symbol: object) -> str | None:
    """``sell`` + ``C-BTC-84000-280926`` becomes ``S C 84000``."""
    if not isinstance(symbol, str) or not symbol:
        return None
    prefix = "S" if side == "sell" else "B" if side == "buy" else None
    match = OPTION_SYMBOL.match(symbol)
    contract = f"{match.group(1)} {match.group(2)}" if match else symbol
    return f"{prefix} {contract}" if prefix else contract


def legs_text(legs: Iterable[tuple[object, object]]) -> str:
    labels: list[str] = []
    for side, symbol in legs:
        label = leg_label(side, symbol)
        if label and label not in labels:
            labels.append(label)
    return ", ".join(labels)


def pnl_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    sign = "+" if amount > 0 else ""
    return f"P&L {sign}{amount} USD"


def strategy_message(
    *,
    event: StrategyEvent,
    strategy_id: str,
    row: Mapping[str, Any],
    legs: Iterable[tuple[object, object]],
    detail: str | None = None,
) -> PushMessage:
    """Title names the asset and strategy; the body lists legs and one short fact."""
    definition = row.get("definition_json") if isinstance(row.get("definition_json"), Mapping) else {}
    instrument = definition.get("instrument") if isinstance(definition.get("instrument"), Mapping) else {}
    asset = str(instrument.get("underlying") or "").strip()
    name = str(definition.get("name") or "Strategy").strip()
    heading = f"{asset} {name}".strip()
    risk_state = row.get("risk_state") if isinstance(row.get("risk_state"), Mapping) else {}
    short_detail = (detail or "").strip()[:DETAIL_LIMIT] or None

    facts: list[str | None]
    if event == "activated":
        title, facts, urgent = f"{heading} activated", [], False
    elif event == "closed":
        reason = EXIT_REASONS.get(str(risk_state.get("exitReason") or "")) or short_detail
        title, facts, urgent = f"{heading} closed", [reason, pnl_text(row.get("realized_pnl"))], False
    elif event == "entry_failed":
        title, facts, urgent = f"{heading} entry needs attention", [short_detail], True
    elif event == "entry_rejected":
        title, facts, urgent = f"{heading} entry not placed", [short_detail], False
    elif event == "exit_failed":
        title, facts, urgent = f"{heading} exit needs attention", [short_detail], True
    else:
        assert_never(event)

    body = " · ".join(part for part in (legs_text(legs), *facts) if part) or "Open the app for details"
    return PushMessage(
        title=title,
        body=body,
        tag=f"strategy-{strategy_id}",
        url=f"/?tab=runs&strategy={strategy_id}",
        urgent=urgent,
    )


Sender = Callable[[dict[str, Any], PushMessage], Awaitable[None]]


class PushNotifier:
    """Stores subscriptions and delivers messages. Disabled when VAPID keys are missing."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        public_key: str | None,
        private_key: str | None,
        subject: str,
        sender: Sender | None = None,
    ) -> None:
        self.pool = pool
        self.public_key = public_key or None
        self.private_key = private_key or None
        self.subject = subject
        self.sender = sender or self._webpush
        self.tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.private_key)

    async def save(self, user_id: str, subscription: PushSubscriptionIn) -> None:
        # A device that changes account moves its endpoint to the new user.
        async with self.pool.connection() as connection:
            await connection.execute(
                """insert into trade.push_subscriptions (endpoint, user_id, p256dh, auth)
                   values (%s, %s, %s, %s)
                   on conflict (endpoint) do update
                   set user_id = excluded.user_id, p256dh = excluded.p256dh,
                       auth = excluded.auth, updated_at = now()""",
                (subscription.endpoint, user_id, subscription.keys.p256dh, subscription.keys.auth),
            )

    async def remove(self, user_id: str, endpoint: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "delete from trade.push_subscriptions where endpoint = %s and user_id = %s", (endpoint, user_id)
            )

    async def _subscriptions(self, user_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "select endpoint, p256dh, auth from trade.push_subscriptions where user_id = %s", (user_id,)
            )
            rows = await result.fetchall()
        return [{"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}} for endpoint, p256dh, auth in rows]

    async def _forget(self, endpoint: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute("delete from trade.push_subscriptions where endpoint = %s", (endpoint,))

    async def send(self, user_id: str, message: PushMessage) -> int:
        """Deliver to every device of the user. Returns how many accepted the message."""
        if not self.enabled:
            return 0
        delivered = 0
        for subscription in await self._subscriptions(user_id):
            try:
                await self.sender(subscription, message)
                delivered += 1
            except WebPushException as error:
                status = getattr(error.response, "status_code", None)
                if status in GONE_STATUSES:
                    await self._forget(subscription["endpoint"])
                    logger.info("Removed expired push subscription user_id=%s status=%s", user_id, status)
                else:
                    logger.warning("Push delivery failed user_id=%s status=%s", user_id, status)
        return delivered

    def spawn(self, work: Coroutine[Any, Any, None], *, name: str) -> None:
        """Run notification work in the background; it can fail without touching the caller."""
        if not self.enabled:
            work.close()
            return
        task = asyncio.create_task(self._guarded(work, name), name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    @staticmethod
    async def _guarded(work: Coroutine[Any, Any, None], name: str) -> None:
        try:
            await work
        except Exception:
            logger.exception("Push notification task failed name=%s", name)

    async def _webpush(self, subscription: dict[str, Any], message: PushMessage) -> None:
        await asyncio.to_thread(
            webpush,
            subscription_info=subscription,
            data=message.payload(),
            vapid_private_key=self.private_key,
            # pywebpush adds aud/exp to the claims dict, so each call gets a fresh one.
            vapid_claims={"sub": self.subject},
            ttl=PUSH_TTL_SECONDS,
            timeout=10,
            headers={"Urgency": "high" if message.urgent else "normal"},
        )
