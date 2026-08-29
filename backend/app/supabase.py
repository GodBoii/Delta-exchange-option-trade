import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings
from .errors import AppError

logger = logging.getLogger(__name__)


class SupabaseAdmin:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self.admin_headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }
        self.signal_tasks: set[asyncio.Task[None]] = set()

    async def close(self) -> None:
        if self.signal_tasks:
            await asyncio.gather(*self.signal_tasks, return_exceptions=True)
        await self.client.aclose()

    async def auth_user(self, access_token: str) -> dict[str, Any] | None:
        response = await self.client.get(
            f"{self.settings.supabase_url}/auth/v1/user",
            headers={
                "apikey": self.settings.supabase_publishable_key,
                "Authorization": f"Bearer {access_token}",
            },
        )
        if response.status_code == 401:
            return None
        if response.is_error:
            raise AppError(502, "Could not verify the application session", "auth_verification_failed")
        return response.json()

    async def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"{self.settings.supabase_url}/rest/v1/{table}", headers=self.admin_headers, params=params
        )
        return self._json(response, "Database query failed")

    async def insert(self, table: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        response = await self.client.post(
            f"{self.settings.supabase_url}/rest/v1/{table}",
            headers={**self.admin_headers, "Prefer": "return=representation"},
            json=payload,
        )
        return self._return_rows(table, response, "Database insert failed")

    async def upsert(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        on_conflict: str,
        ignore_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        resolution = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
        response = await self.client.post(
            f"{self.settings.supabase_url}/rest/v1/{table}",
            headers={**self.admin_headers, "Prefer": f"resolution={resolution},return=representation"},
            params={"on_conflict": on_conflict},
            json=payload,
        )
        return self._return_rows(table, response, "Database upsert failed")

    async def update(self, table: str, payload: dict[str, Any], params: dict[str, str]) -> list[dict[str, Any]]:
        response = await self.client.patch(
            f"{self.settings.supabase_url}/rest/v1/{table}",
            headers={**self.admin_headers, "Prefer": "return=representation"},
            params=params,
            json=payload,
        )
        return self._return_rows(table, response, "Database update failed")

    async def delete(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = await self.client.request(
            "DELETE",
            f"{self.settings.supabase_url}/rest/v1/{table}",
            headers={**self.admin_headers, "Prefer": "return=representation"},
            params=params,
        )
        return self._return_rows(table, response, "Database delete failed")

    def _return_rows(self, table: str, response: httpx.Response, fallback: str) -> list[dict[str, Any]]:
        rows = self._json(response, fallback)
        if table in {"automation_agent_runs", "strategies"}:
            for row in rows:
                task = asyncio.create_task(self._publish_signal(table, row))
                self.signal_tasks.add(task)
                task.add_done_callback(self.signal_tasks.discard)
        return rows

    async def _publish_signal(self, table: str, row: dict[str, Any]) -> None:
        if not self.settings.convex_url or not self.settings.convex_sync_secret:
            return
        try:
            response = await self.client.post(
                f"{self.settings.convex_url.rstrip('/')}/api/mutation",
                timeout=2,
                json={
                    "path": "signals:publish",
                    "format": "json",
                    "args": {
                        "syncSecret": self.settings.convex_sync_secret,
                        "userId": str(row["user_id"]),
                        "scope": "automation" if table == "automation_agent_runs" else "strategies",
                        "entityId": str(row["id"]),
                        "status": str(row.get("status") or "deleted"),
                        **({"outcome": str(row["outcome"])} if row.get("outcome") else {}),
                    },
                },
            )
            response.raise_for_status()
            if response.json().get("status") != "success":
                raise RuntimeError("Convex rejected the signal")
        except Exception as error:
            logger.warning("Convex signal publish failed table=%s id=%s error=%s", table, row.get("id"), error)

    async def rpc(self, function: str, payload: dict[str, Any]) -> Any:
        response = await self.client.post(
            f"{self.settings.supabase_url}/rest/v1/rpc/{function}", headers=self.admin_headers, json=payload
        )
        result = self._json(response, "Database function failed")
        if function == "claim_automation_agent_run" and result:
            for row in result:
                task = asyncio.create_task(self._publish_signal("automation_agent_runs", {**row, "status": "running"}))
                self.signal_tasks.add(task)
                task.add_done_callback(self.signal_tasks.discard)
        return result

    async def signed_storage_url(self, bucket: str, path: str, expires_in: int = 3_600) -> str:
        encoded_path = quote(path, safe="/")
        response = await self.client.post(
            f"{self.settings.supabase_url}/storage/v1/object/sign/{bucket}/{encoded_path}",
            headers=self.admin_headers,
            json={"expiresIn": expires_in},
        )
        payload = self._json(response, "Could not sign the chart image")
        signed = str(payload.get("signedURL") or payload.get("signedUrl") or "")
        if not signed:
            raise AppError(500, "Supabase returned no signed chart URL", "chart_signing_failed")
        if signed.startswith(("http://", "https://")):
            return signed
        if not signed.startswith("/"):
            signed = f"/{signed}"
        return f"{self.settings.supabase_url}/storage/v1{signed}"

    @staticmethod
    def _json(response: httpx.Response, fallback: str) -> Any:
        if response.is_error:
            try:
                detail = response.json().get("message", fallback)
            except Exception:
                detail = fallback
            raise AppError(500, str(detail), "database_error")
        if not response.content:
            return []
        return response.json()
