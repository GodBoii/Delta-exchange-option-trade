"""Application data access: local PostgreSQL for all records, Supabase only for identity.

Callers keep the PostgREST-style ``select/insert/update`` interface. Runtime tables are
served from ``trade.*``; research reports and charts from ``ai.*``. Supabase supplies
authentication and a background mirror of local profiles.
"""

from typing import Any

import httpx
from psycopg_pool import AsyncConnectionPool

from .chart_images import ChartImages
from .config import Settings
from .errors import AppError
from .local_runtime import TABLES as LOCAL_TABLES
from .local_runtime import LocalRuntimeStore
from .profiles import Profiles
from .report_store import REPORT_FIELDS, ReportStore
from .supabase_auth import SupabaseAuth

TABLES = frozenset({*LOCAL_TABLES, "automation_settings"})

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
        self.profiles = Profiles(pool, self.client, settings)

    async def close(self) -> None:
        await self.profiles.close()
        await self.client.aclose()

    async def auth_user(self, access_token: str) -> dict[str, Any] | None:
        return await self.auth.user(access_token)

    async def registered_profiles(
        self, *, offset: int, limit: int, search: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        return await self.profiles.page(offset=offset, limit=limit, search=search)

    async def registered_profile(self, user_id: str) -> dict[str, Any] | None:
        return await self.profiles.get(user_id)

    async def profile(self, user_id: str, *, max_age: float | None = None) -> dict[str, Any]:
        # Kept for caller compatibility. Local PostgreSQL is authoritative, with no remote TTL.
        return await self.profiles.ensure(user_id)

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
        if columns == "*":
            metadata = "*"
        else:
            metadata = ",".join(dict.fromkeys(["id", *(c for c in columns.split(",") if c not in REPORT_FIELDS)]))
        rows = await self.runtime.select(table, {**params, "select": metadata})
        rows = await self.reports.hydrate(rows, columns)
        return rows if columns == "*" else [{name: row.get(name) for name in columns.split(",")} for row in rows]

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
