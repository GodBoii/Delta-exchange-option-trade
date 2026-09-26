"""Verify Supabase access tokens in-process with the project's published signing keys.

Asymmetric tokens (ES256/RS256) are checked locally, so an API request does not wait on
a network call to Supabase. Tokens signed with a symmetric legacy secret cannot be
verified without that secret, so they fall back to Supabase's user endpoint.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt

from .errors import AppError

logger = logging.getLogger(__name__)
ASYMMETRIC = frozenset({"ES256", "RS256"})
KEY_TTL_SECONDS = 600
MISSING_KEY_RETRY_SECONDS = 30


class SupabaseAuth:
    def __init__(self, supabase_url: str, publishable_key: str, client: httpx.AsyncClient) -> None:
        self.base_url = supabase_url.rstrip("/")
        self.issuer = f"{self.base_url}/auth/v1"
        self.publishable_key = publishable_key
        self.client = client
        self.keys: dict[str, jwt.PyJWK] = {}
        self.keys_loaded_at = 0.0
        self.lock = asyncio.Lock()

    async def user(self, token: str) -> dict[str, Any] | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            return None
        algorithm = header.get("alg")
        kid = header.get("kid")
        if algorithm not in ASYMMETRIC or not isinstance(kid, str):
            return await self.remote_user(token)
        key = await self.signing_key(kid)
        if key is None:
            return await self.remote_user(token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience="authenticated",
                issuer=self.issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
                leeway=5,
            )
        except jwt.InvalidTokenError:
            return None
        if claims.get("role") != "authenticated":
            return None
        return {
            "id": str(claims["sub"]),
            "email": claims.get("email"),
            "phone": claims.get("phone"),
            "user_metadata": claims.get("user_metadata") or {},
            "app_metadata": claims.get("app_metadata") or {},
            "role": claims.get("role"),
            "is_anonymous": bool(claims.get("is_anonymous", False)),
        }

    async def signing_key(self, kid: str) -> jwt.PyJWK | None:
        now = time.monotonic()
        key = self.keys.get(kid)
        if key is not None and now - self.keys_loaded_at < KEY_TTL_SECONDS:
            return key
        # Rotation publishes the new key before tokens use it; a short retry window avoids refetch storms.
        if key is None and now - self.keys_loaded_at < MISSING_KEY_RETRY_SECONDS:
            return None
        async with self.lock:
            if now - self.keys_loaded_at >= MISSING_KEY_RETRY_SECONDS or kid not in self.keys:
                await self.refresh_keys()
        return self.keys.get(kid) or key

    async def refresh_keys(self) -> None:
        self.keys_loaded_at = time.monotonic()
        try:
            response = await self.client.get(f"{self.issuer}/.well-known/jwks.json", timeout=5)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Could not refresh Supabase signing keys: %s", type(error).__name__)
            return
        keys: dict[str, jwt.PyJWK] = {}
        for item in document.get("keys", []) if isinstance(document, dict) else []:
            if not isinstance(item, dict) or item.get("alg") not in ASYMMETRIC or not item.get("kid"):
                continue
            try:
                keys[str(item["kid"])] = jwt.PyJWK(item)
            except jwt.PyJWKError:
                logger.warning("Ignoring an unusable Supabase signing key kid=%s", item.get("kid"))
        if keys:
            self.keys = keys

    async def remote_user(self, token: str) -> dict[str, Any] | None:
        try:
            response = await self.client.get(
                f"{self.issuer}/user",
                headers={"apikey": self.publishable_key, "Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as error:
            raise AppError(502, "Could not verify the application session", "auth_verification_failed") from error
        if response.status_code in {401, 403}:
            return None
        if response.is_error:
            raise AppError(502, "Could not verify the application session", "auth_verification_failed")
        return response.json()
