"""Local profile authority with a coalescing, durable Supabase mirror."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import httpx
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .errors import AppError

logger = logging.getLogger(__name__)
COLUMNS = "id,email,display_name,phone_number,avatar_url,user_type,created_at"
EDITABLE = frozenset({"display_name", "phone_number", "avatar_url", "user_type"})


class Profiles:
    def __init__(self, pool: AsyncConnectionPool, client: httpx.AsyncClient, settings: Any) -> None:
        self.pool, self.client = pool, client
        self.url = f"{settings.supabase_url}/rest/v1/profiles"
        self.headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        }
        self.writer = getattr(settings, "trading_writer_enabled", True)
        self.discovery_lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.stopping = asyncio.Event()
        self.last_error: str | None = None
        self.next_discovery = 0.0

    async def get(self, user_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            result = await connection.execute("select data from trade.profiles where id=%s", (user_id,))
            row = await result.fetchone()
        return row[0] if row else None

    async def ensure(self, user_id: str) -> dict[str, Any]:
        row = await self.get(user_id)
        if row is not None:
            return row
        if not self.writer:
            raise AppError(503, "Profile initialization is pending on the writer", "profile_unavailable")
        # Trusted service-role row only: user-editable Auth metadata never grants owner access.
        async with self.discovery_lock:
            row = await self.get(user_id)
            if row is not None:
                return row
            try:
                response = await self.client.get(
                    self.url, headers=self.headers, params={"select": COLUMNS, "id": f"eq.{user_id}", "limit": "1"}
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Missing trusted profile")
                await self.import_rows(rows)
            except (httpx.HTTPError, ValueError, TypeError) as error:
                raise AppError(503, "Profile initialization is unavailable", "profile_unavailable") from error
            row = await self.get(user_id)
            if row is None:
                raise AppError(503, "Profile initialization is unavailable", "profile_unavailable")
            return row

    async def import_rows(self, rows: list[dict[str, Any]]) -> None:
        async with self.pool.connection() as connection:
            for row in rows:
                if not isinstance(row, dict) or not row.get("id") or row.get("user_type") not in {"user", "owner"}:
                    raise ValueError("Invalid trusted profile")
                value = {key: row.get(key) for key in COLUMNS.split(",")}
                await connection.execute(
                    "insert into trade.profiles(id,data) values (%s,%s) on conflict do nothing",
                    (str(row["id"]), Jsonb(value)),
                )

    async def discover(self) -> None:
        if not self.writer:
            return
        async with self.discovery_lock:
            async with self.pool.connection() as connection:
                result = await connection.execute(
                    "select value from trade.profile_sync_state where key='discovered_at'"
                )
                checkpoint = await result.fetchone()
            started = datetime.now(UTC).isoformat()
            offset = 0
            while True:
                params = {"select": COLUMNS, "order": "id.asc", "offset": str(offset), "limit": "100"}
                if checkpoint:
                    params["updated_at"] = f"gte.{checkpoint[0]}"
                response = await self.client.get(self.url, headers=self.headers, params=params)
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list):
                    raise ValueError("Invalid profile page")
                await self.import_rows(rows)
                if len(rows) < 100:
                    break
                offset += len(rows)
            async with self.pool.connection() as connection:
                await connection.execute(
                    """insert into trade.profile_sync_state(key,value) values ('discovered_at',%s)
                       on conflict(key) do update set value=excluded.value""", (started,),
                )

    async def page(self, *, offset: int, limit: int, search: str | None = None) -> tuple[list[dict], int]:
        # Only initial bootstrap needs a remote listing; routine owner pages read local rows.
        if self.writer:
            async with self.pool.connection() as connection:
                result = await connection.execute("select 1 from trade.profile_sync_state where key='discovered_at'")
                bootstrapped = await result.fetchone()
            if not bootstrapped:
                await self.discover()
        pattern = f"%{search.replace('%', r'\%').replace('_', r'\_')}%" if search else None
        where = "(%s::text is null or data->>'display_name' ilike %s or data->>'email' ilike %s)"
        async with self.pool.connection() as connection:
            count = await connection.execute(f"select count(*) from trade.profiles where {where}",
                                             (pattern, pattern, pattern))
            total = (await count.fetchone())[0]
            result = await connection.execute(
                f"""select data from trade.profiles where {where}
                    order by data->>'user_type',data->>'created_at',id limit %s offset %s""",
                (pattern, pattern, pattern, max(1, min(limit, 100)), max(0, offset)),
            )
            return [row[0] for row in await result.fetchall()], total

    async def save(self, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not self.writer or not changes or changes.keys() - EDITABLE:
            raise AppError(422, "Invalid profile update", "profile_update_invalid")
        if "user_type" in changes and changes["user_type"] not in {"owner", "user"}:
            raise AppError(422, "Invalid profile role", "profile_update_invalid")
        await self.ensure(user_id)
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """update trade.profiles set data=data || %s, revision=revision+1,
                   sync_attempts=0,next_sync_at=now(),last_sync_error=null,updated_at=now()
                   where id=%s and data is distinct from (data || %s) returning data""",
                (Jsonb(changes), user_id, Jsonb(changes)),
            )
            saved = await result.fetchone()
        return saved[0] if saved else await self.ensure(user_id)

    async def mirror(self) -> None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """select id,data,revision,sync_attempts from trade.profiles
                   where mirrored_revision<revision and next_sync_at<=now() order by next_sync_at limit 25"""
            )
            rows = await cursor.fetchall()
        for row in rows:
            try:
                response = await self.client.post(
                    self.url, params={"on_conflict": "id"},
                    headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                    json={key: row["data"].get(key) for key in COLUMNS.split(",")},
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                code = (
                    f"http_{error.response.status_code}"
                    if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
                )
                delay = min(3600, 30 * 2 ** min(row["sync_attempts"], 7))
                async with self.pool.connection() as connection:
                    await connection.execute(
                        """update trade.profiles set sync_attempts=sync_attempts+1,last_sync_error=%s,
                           next_sync_at=now()+(%s * interval '1 second') where id=%s and revision=%s""",
                        (code, delay, row["id"], row["revision"]),
                    )
                logger.warning("Profile mirror delayed code=%s", code)
                continue
            async with self.pool.connection() as connection:
                await connection.execute(
                    """update trade.profiles set mirrored_revision=greatest(mirrored_revision,%s),
                       sync_attempts=0,last_sync_error=null where id=%s""", (row["revision"], row["id"]),
                )

    async def status(self) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """select count(*),max(last_sync_error) from trade.profiles
                   where mirrored_revision<revision"""
            )
            pending, error = await result.fetchone()
        return {"running": bool(self.task and not self.task.done()), "pending": pending,
                "lastError": error or self.last_error}

    def start(self) -> None:
        if self.writer:
            self.task = asyncio.create_task(self.run(), name="profile-mirror")

    async def run(self) -> None:
        while not self.stopping.is_set():
            try:
                now = asyncio.get_running_loop().time()
                if now >= self.next_discovery:
                    await self.discover()
                    self.next_discovery = now + 300
                await self.mirror()
                self.last_error = None
            except Exception as error:
                self.last_error = type(error).__name__
                self.next_discovery = asyncio.get_running_loop().time() + 300
                logger.warning("Profile synchronization deferred code=%s", self.last_error)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), 30)

    async def close(self) -> None:
        self.stopping.set()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
