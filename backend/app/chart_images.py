"""Serve analysis chart images from PostgreSQL through short-lived signed links.

Browsers load charts with plain ``<img>`` requests, which cannot carry the login token.
The API authorizes the run when it lists charts and returns a link signed with a key
derived from ``ANALYSIS_SERVICE_SECRET``. Every API replica can verify and serve it.
"""

import hashlib
import hmac
import logging
import re
import time
from typing import Any
from urllib.parse import quote
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from .errors import AppError

logger = logging.getLogger(__name__)
CHART_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
DELETE_BATCH = 1_000


class ChartImages:
    def __init__(self, pool: AsyncConnectionPool, secret: str, link_seconds: int) -> None:
        self.pool = pool
        self.key = hmac.new(secret.encode(), b"trade-cognition/chart-link/v1", hashlib.sha256).digest()
        self.link_seconds = link_seconds

    def _signature(self, run_id: str, chart_id: str, expires: int) -> str:
        message = f"{run_id}\n{chart_id}\n{expires}".encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def signed_path(self, run_id: str, chart_id: str, now: float | None = None) -> str:
        # Expiry is aligned to the link window so repeated listings reuse one cacheable URL.
        current = int(now if now is not None else time.time())
        expires = (current // self.link_seconds + 2) * self.link_seconds
        signature = self._signature(run_id, chart_id, expires)
        return f"/api/charts/{run_id}/{quote(chart_id, safe='')}?expires={expires}&signature={signature}"

    def verify(self, run_id: str, chart_id: str, expires: int, signature: str) -> None:
        if not CHART_ID.fullmatch(chart_id) or expires < time.time():
            raise AppError(404, "Chart link expired", "chart_not_found")
        try:
            UUID(run_id)
        except ValueError as error:
            raise AppError(404, "Chart not found", "chart_not_found") from error
        if not hmac.compare_digest(self._signature(run_id, chart_id, expires), signature):
            raise AppError(404, "Chart not found", "chart_not_found")

    async def available(self, run_ids: list[str]) -> set[tuple[str, str]]:
        valid = [run_id for run_id in run_ids if _is_uuid(run_id)]
        if not valid:
            return set()
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "select run_id::text, chart_id from ai.chart_images where run_id = any(%s::uuid[])", (valid,)
            )
            return {(row[0], row[1]) for row in await result.fetchall()}

    async def content(self, run_id: str, chart_id: str) -> bytes:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "select content from ai.chart_images where run_id=%s and chart_id=%s", (run_id, chart_id)
            )
            row = await result.fetchone()
        if not row:
            raise AppError(404, "Chart not found", "chart_not_found")
        return bytes(row[0])

    async def delete_expired(self, retention_days: int) -> int:
        """Delete charts past retention in small batches so no single statement holds long locks."""
        removed = 0
        while True:
            async with self.pool.connection() as connection:
                result = await connection.execute(
                    """delete from ai.chart_images where ctid in (
                           select ctid from ai.chart_images
                           where created_at < now() - make_interval(days => %s) limit %s)""",
                    (retention_days, DELETE_BATCH),
                )
                count = result.rowcount
            removed += count
            if count < DELETE_BATCH:
                return removed


def chart_link(charts: ChartImages, run_id: str, chart: dict[str, Any]) -> dict[str, str] | None:
    chart_id = chart.get("id")
    if not isinstance(chart_id, str) or not CHART_ID.fullmatch(chart_id):
        return None
    label = str(chart.get("label") or "Market chart")
    return {
        "id": chart_id,
        "label": label,
        "altText": str(chart.get("altText") or label),
        "url": charts.signed_path(run_id, chart_id),
    }


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True
