"""Application data access: local PostgreSQL for all records, Supabase only for identity.

Callers keep the PostgREST-style ``select/insert/update`` interface. Runtime tables are
served from ``trade.*``; research reports and charts from ``ai.*``. Supabase supplies the
login token check and the ``profiles`` row, which is cached briefly per user.
"""

import logging
import time
from collections import OrderedDict
from typing import Any

import httpx
from psycopg_pool import AsyncConnectionPool

from .chart_images import ChartImages
from .config import Settings
from .errors import AppError
from .local_runtime import TABLES as LOCAL_TABLES
from .local_runtime import LocalRuntimeStore
from .report_store import REPORT_FIELDS, ReportStore
from .supabase_auth import SupabaseAuth

logger = logging.getLogger(__name__)
TABLES = frozenset({*LOCAL_TABLES, "automation_settings"})
PROFILE_COLUMNS = "display_name,avatar_url,phone_number,user_type"
PROFILE_CACHE_SIZE = 10_000


class Database:
    def __init__(self, settings: Settings, pool: AsyncConnectionPool) -> None:
        self.settings = settings
        self.pool = pool
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self.runtime = LocalRuntimeStore(pool)
        self.local_data = self.runtime.data
        self.reports = ReportStore(pool)
        self.charts = ChartImages(pool, settings.analysis_service_secret, settings.chart_link_seconds)
        self.auth = SupabaseAuth(settings.supabase_url, settings.supabase_publishable_key, self.client)
        self.admin_headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        }
        self.profile_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    async def close(self) -> None:
        await self.client.aclose()

    async def auth_user(self, access_token: str) -> dict[str, Any] | None:
        return await self.auth.user(access_token)

    async def profile(self, user_id: str) -> dict[str, Any]:
        """Return the Supabase profile row, or an empty mapping when the user has none."""
        ttl = getattr(self.settings, "auth_profile_cache_seconds", 60)
        cached = self.profile_cache.get(user_id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < ttl:
            self.profile_cache.move_to_end(user_id)
            return cached[1]
        try:
            response = await self.client.get(
                f"{self.settings.supabase_url}/rest/v1/profiles",
                headers=self.admin_headers,
                params={"select": PROFILE_COLUMNS, "id": f"eq.{user_id}", "limit": "1"},
            )
            response.raise_for_status()
            rows = response.json()
        except (httpx.HTTPError, ValueError) as error:
            if cached is not None:
                logger.warning("Serving a cached profile after lookup failed: %s", type(error).__name__)
                return cached[1]
            raise AppError(503, "Profile service is unavailable", "profile_unavailable") from error
        profile = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        self.profile_cache[user_id] = (now, profile)
        self.profile_cache.move_to_end(user_id)
        while len(self.profile_cache) > PROFILE_CACHE_SIZE:
            self.profile_cache.popitem(last=False)
        return profile

    @staticmethod
    def _require_table(table: str) -> None:
        if table not in TABLES:
            raise AppError(500, f"Unknown application table: {table}", "unsupported_record_query")

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        if table == "automation_market_snapshots":
            return await self.reports.snapshots(params)
        self._require_table(table)
        if table != "automation_agent_runs":
            return await self.runtime.select(table, params)
        # Preserve identifiers while joining large reports kept outside the runtime row.
        columns = params.get("select", "*")
        rows = await self.runtime.select(table, {**params, "select": "*"})
        return await self.reports.hydrate(rows, columns)

    async def insert(self, table: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self._require_table(table)
        return await self.runtime.write(table, payload)

    async def upsert(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        on_conflict: str,
        ignore_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        self._require_table(table)
        if ignore_duplicates:
            existing = await self.runtime.select(table, {on_conflict: f"eq.{payload[on_conflict]}"})
            if existing:
                return []
        return await self.runtime.write(table, payload, on_conflict)

    async def update(self, table: str, payload: dict[str, Any], params: dict[str, str]) -> list[dict[str, Any]]:
        self._require_table(table)
        if table == "automation_agent_runs" and any(key in REPORT_FIELDS for key in payload):
            rows = await self.runtime.select(table, {**params, "select": "id"})
            for row in rows:
                await self.reports.save(row["id"], payload)
            payload = {key: value for key, value in payload.items() if key not in REPORT_FIELDS}
        return await self.runtime.update(table, payload, params)

    async def delete(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        self._require_table(table)
        return await self.runtime.update(table, {}, params, remove=True)

    async def rpc(self, function: str, payload: dict[str, Any]) -> Any:
        return await self.runtime.rpc(function, payload)
