from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings
from .errors import AppError


class SupabaseAdmin:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self.admin_headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
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
        return self._json(response, "Database insert failed")

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
        return self._json(response, "Database upsert failed")

    async def update(self, table: str, payload: dict[str, Any], params: dict[str, str]) -> list[dict[str, Any]]:
        response = await self.client.patch(
            f"{self.settings.supabase_url}/rest/v1/{table}",
            headers={**self.admin_headers, "Prefer": "return=representation"},
            params=params,
            json=payload,
        )
        return self._json(response, "Database update failed")

    async def delete(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = await self.client.request(
            "DELETE",
            f"{self.settings.supabase_url}/rest/v1/{table}",
            headers={**self.admin_headers, "Prefer": "return=representation"},
            params=params,
        )
        return self._json(response, "Database delete failed")

    async def rpc(self, function: str, payload: dict[str, Any]) -> Any:
        response = await self.client.post(
            f"{self.settings.supabase_url}/rest/v1/rpc/{function}", headers=self.admin_headers, json=payload
        )
        return self._json(response, "Database function failed")

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
