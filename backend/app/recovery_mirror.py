"""Send committed local outbox events to Convex without blocking trading."""

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .application_data import ConvexApplicationData

logger = logging.getLogger(__name__)
TABLES = (
    "users",
    "system_settings",
    "saved_strategies",
    "strategies",
    "executions",
    "execution_orders",
    "strategy_capital_slots",
    "strategy_proposals",
    "analysis_jobs",
    "order_intents",
    "product_claims",
    "exchange_fills",
)


class RecoveryMirror:
    def __init__(self, pool: AsyncConnectionPool, client: httpx.AsyncClient, url: str, secret: str) -> None:
        self.pool = pool
        self.data = ConvexApplicationData(url, secret, client)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.last_error: str | None = None
        self.last_success_at: str | None = None
        self.last_manifest_day: str | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="trade-recovery-mirror")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.task, timeout=10)

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                delivered = await self.deliver_once()
                day = datetime.now(UTC).date().isoformat()
                if not delivered and self.last_manifest_day != day:
                    await self.save_manifest(day)
                    await self.prune()
                    self.last_manifest_day = day
                self.last_error = None
                if delivered:
                    continue
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("Recovery mirror cycle failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)

    async def deliver_once(self) -> bool:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select id,entity_type,entity_key,revision,operation,payload
                   from trade.recovery_outbox where delivered_at is null
                   order by id limit 25 for update skip locked"""
            )
            rows = await cursor.fetchall()
            if not rows:
                return False
            items = []
            for row in rows:
                payload_json = json.dumps(row["payload"], separators=(",", ":"), default=str)
                if len(payload_json) > 262144:
                    raise ValueError(f"Recovery record exceeds Convex limit: {row['entity_type']}")
                items.append(
                    {
                        "entityType": row["entity_type"],
                        "entityKey": row["entity_key"],
                        "revision": row["revision"],
                        "operation": row["operation"],
                        **({"payloadJson": payload_json} if row["operation"] == "upsert" else {}),
                    }
                )
            await self.data.request("recovery:applyBatch", {"items": items}, mutation=True)
            await cursor.execute(
                "update trade.recovery_outbox set delivered_at=now() where id=any(%s)",
                ([row["id"] for row in rows],),
            )
            self.last_success_at = datetime.now(UTC).isoformat()
            return True

    async def status(self) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select count(*) as pending,min(created_at) as oldest,max(id) as last_id
                   from trade.recovery_outbox where delivered_at is null"""
            )
            row = await cursor.fetchone()
        oldest = row["oldest"]
        lag_seconds = max(0, (datetime.now(UTC) - oldest).total_seconds()) if oldest else 0
        return {
            "pending": row["pending"],
            "oldestPendingAt": oldest.isoformat() if oldest else None,
            "lagSeconds": lag_seconds,
            "lagAlert": lag_seconds > 60,
            "lastSuccessAt": self.last_success_at,
            "lastError": self.last_error,
        }

    async def save_manifest(self, day: str) -> None:
        counts: dict[str, int] = {}
        digest = hashlib.sha256()
        async with self.pool.connection() as connection:
            for table in TABLES:
                target = sql.Identifier("trade", table)
                result = await connection.execute(sql.SQL("select count(*) from {}").format(target))
                counts[table] = (await result.fetchone())[0]
                if table in {"users", "system_settings", "order_intents", "product_claims", "exchange_fills"}:
                    continue
                records = await connection.execute(sql.SQL("select id,revision from {} order by id").format(target))
                async for identifier, revision in records:
                    digest.update(f"{table}:{identifier}:{revision}\n".encode())
            result = await connection.execute(
                "select coalesce(max(id),0) from trade.recovery_outbox where delivered_at is not null"
            )
            last_id = (await result.fetchone())[0]
        await self.data.request(
            "recovery:saveManifest",
            {
                "day": day,
                "lastOutboxId": last_id,
                "countsJson": json.dumps(counts, sort_keys=True),
                "checksum": digest.hexdigest(),
            },
            mutation=True,
        )

    async def prune(self) -> None:
        cutoff = int((datetime.now(UTC) - timedelta(days=90)).timestamp() * 1000)
        for _ in range(100):
            removed = await self.data.request("recovery:prunePage", {"before": cutoff}, mutation=True)
            if removed < 25:
                return
        logger.warning("Recovery retention backlog exceeds 2500 records")
