"""Report the trading writer's outbound address, including freshness."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any

from psycopg.rows import dict_row

from .database import Database
from .errors import AppError

logger = logging.getLogger(__name__)
REFRESH_SECONDS = 300


class ServerIp:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.lock = asyncio.Lock()

    async def read(self) -> dict[str, Any]:
        async with self.db.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select outbound_ip,ip_checked_at from trade.system_settings where key='main'")
            row = await cursor.fetchone()
        checked = row["ip_checked_at"] if row else None
        return {
            "address": str(row["outbound_ip"]) if row and row["outbound_ip"] else None,
            "checkedAt": checked,
            "stale": checked is None or (datetime.now(UTC) - checked).total_seconds() > REFRESH_SECONDS * 2,
        }

    async def refresh(self) -> dict[str, Any]:
        async with self.lock:
            try:
                response = await self.db.client.get("https://api.ipify.org", timeout=5)
                response.raise_for_status()
                address = str(ip_address(response.text.strip()))
            except Exception as error:
                logger.warning("Outbound IP refresh failed: %s", type(error).__name__)
                raise AppError(
                    502, "Could not verify the server IP. The last checked address is retained", "server_ip_unavailable"
                ) from error
            await self.db.local_data.request("accounts:updateOutboundIp", {"ip": address}, mutation=True)
            return await self.read()

    async def run(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            with suppress(AppError):
                await self.refresh()
