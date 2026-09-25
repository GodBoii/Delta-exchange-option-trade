"""Exchange secrets are encrypted before leaving the backend."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from .application_data import ConvexApplicationData
from .errors import AppError


class CredentialStore:
    def __init__(self, data: ConvexApplicationData, key: str) -> None:
        if not key:
            raise ValueError("CONVEX_CREDENTIAL_KEY is required for encrypted credential storage")
        self.data = data
        self.cipher = Fernet(key.encode())
        self.key = key.encode()

    async def read(self, user_id: str) -> dict[str, str]:
        row = await self.data.request("accounts:credentials", {"userId": user_id})
        if not row or row.get("status") != "connected" or not row.get("ciphertext"):
            raise AppError(401, "Connect Delta Exchange to continue", "delta_not_connected")
        try:
            value = json.loads(self.cipher.decrypt(row["ciphertext"].encode()))
        except (InvalidToken, ValueError, TypeError) as error:
            raise AppError(503, "Exchange credentials could not be decrypted", "credentials_unavailable") from error
        if (
            not isinstance(value, dict)
            or value.get("user_id") != user_id
            or value.get("delta_user_id") != row["delta_user_id"]
            or value.get("environment") != "production"
            or not value.get("api_key")
            or not value.get("api_secret")
        ):
            raise AppError(503, "Exchange credential identity mismatch", "credentials_unavailable")
        return {key: str(value[key]) for key in ("api_key", "api_secret", "delta_user_id")}

    def seal(self, user_id: str, api_key: str, api_secret: str, profile: dict[str, Any]) -> dict[str, Any]:
        value = {
            "user_id": user_id,
            "delta_user_id": str(profile["id"]),
            "environment": "production",
            "api_key": api_key,
            "api_secret": api_secret,
        }
        plaintext = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        record = {
            "id": str(profile.get("connection_id") or uuid4()),
            "user_id": user_id,
            "delta_user_id": str(profile["id"]),
            "account_name": profile.get("account_name") or "Main",
            "email_masked": profile.get("email_masked"),
            "environment": "production",
            "status": "connected",
            "updated_at": datetime.now(UTC).isoformat(),
            "ciphertext": self.cipher.encrypt(plaintext).decode(),
            "fingerprint": hmac.new(self.key, plaintext, hashlib.sha256).hexdigest(),
        }
        return record

    async def save(self, user_id: str, api_key: str, api_secret: str, profile: dict[str, Any]) -> str:
        current = await self.data.request("accounts:credentials", {"userId": user_id})
        record = self.seal(user_id, api_key, api_secret, profile)
        if current:
            record["id"] = current["id"]
        if current and current["fingerprint"] == record["fingerprint"] and current["status"] == "connected":
            return str(current["id"])
        return await self.data.request(
            "accounts:saveConnection",
            {
                "value": record,
                "expectedFingerprint": current["fingerprint"] if current else None,
            },
            mutation=True,
        )


def account_store(db: Any) -> CredentialStore | None:
    settings = getattr(db, "settings", None)
    if not getattr(settings, "convex_accounts_enabled", False):
        return None
    data = db.local_data if getattr(settings, "application_storage", "convex") == "local" else (
        ConvexApplicationData(settings.convex_url, settings.convex_trading_secret, db.client)
    )
    return CredentialStore(data, settings.convex_credential_key)
