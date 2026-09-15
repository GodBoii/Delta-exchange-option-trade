from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from app.credential_store import CredentialStore
from app.errors import AppError


async def test_secrets_leave_the_backend_only_as_ciphertext():
    data = SimpleNamespace(request=AsyncMock(side_effect=[None, "connection"]))
    store = CredentialStore(data, Fernet.generate_key().decode())
    await store.save("owner", "test-api-key", "test-api-secret", {"id": "account", "connection_id": "original-id"})
    payload = data.request.call_args.args[1]["value"]
    assert payload["id"] == "original-id"
    assert "test-api-key" not in str(payload)
    assert "test-api-secret" not in str(payload)
    data.request = AsyncMock(return_value=payload)
    assert await store.read("owner") == {
        "api_key": "test-api-key",
        "api_secret": "test-api-secret",
        "delta_user_id": "account",
    }


async def test_wrong_key_or_swapped_account_never_returns_credentials():
    data = SimpleNamespace(request=AsyncMock(side_effect=[None, "connection"]))
    store = CredentialStore(data, Fernet.generate_key().decode())
    await store.save("owner", "test-key", "test-secret", {"id": "account"})
    record = data.request.call_args.args[1]["value"]
    data.request = AsyncMock(return_value=record)
    with pytest.raises(AppError, match="decrypted"):
        await CredentialStore(data, Fernet.generate_key().decode()).read("owner")
    with pytest.raises(AppError, match="identity"):
        await store.read("other-owner")
    data.request = AsyncMock(return_value={**record, "delta_user_id": "other-account"})
    with pytest.raises(AppError, match="identity"):
        await store.read("owner")


async def test_revoked_credentials_remain_unavailable():
    data = SimpleNamespace(request=AsyncMock(return_value={"status": "revoked", "ciphertext": None}))
    with pytest.raises(AppError) as error:
        await CredentialStore(data, Fernet.generate_key().decode()).read("owner")
    assert error.value.code == "delta_not_connected"
