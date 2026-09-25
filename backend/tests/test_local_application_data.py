"""Check local library revisions and encrypted connection lifecycle."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from app.errors import AppError
from app.local_application_data import LocalApplicationData

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


@pytest.fixture(scope="module")
def event_loop_policy():
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
async def data():
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        yield LocalApplicationData(pool)


@pytest.mark.asyncio
async def test_library_save_revision_and_soft_delete(data: LocalApplicationData):
    user_id = str(uuid4())
    identifier = str(uuid4())
    definition = {"name": "Test spread", "legs": [{"position": "sell"}], "enabledForAi": True}
    payload = {
        "id": identifier,
        "name": "Test spread",
        "definitionJson": json.dumps(definition),
        "enabled": True,
        "expectedVersion": None,
    }
    created = await data.library_save(user_id, payload)
    assert created["version"] == 1 and created["user_id"] == user_id
    assert (await data.request("library:serverGet", {"userId": user_id, "id": identifier}))["id"] == identifier
    assert await data.request("library:serverGet", {"userId": str(uuid4()), "id": identifier}) is None
    updated = await data.library_save(
        user_id,
        {
            **payload,
            "enabled": False,
            "definitionJson": json.dumps(
                {
                    **definition,
                    "enabledForAi": False,
                }
            ),
            "expectedVersion": 1,
        },
    )
    assert updated["version"] == 2
    with pytest.raises(AppError):
        await data.library_save(user_id, {**payload, "expectedVersion": 1})
    await data.library_remove(user_id, identifier, 2)
    assert await data.request("library:serverGet", {"userId": user_id, "id": identifier}) is None


@pytest.mark.asyncio
async def test_connection_rotation_requires_fingerprint(data: LocalApplicationData):
    user_id = str(uuid4())
    connection = {
        "id": str(uuid4()),
        "user_id": user_id,
        "delta_user_id": "1001",
        "status": "connected",
        "ciphertext": "encrypted",
        "fingerprint": "first",
        "environment": "production",
        "account_name": "Main",
        "email_masked": None,
        "updated_at": "2026-09-24T12:00:00+00:00",
    }
    await data.request("accounts:saveConnection", {"value": connection, "expectedFingerprint": None}, mutation=True)
    with pytest.raises(AppError):
        await data.request(
            "accounts:saveConnection",
            {
                "value": {**connection, "fingerprint": "second"},
                "expectedFingerprint": None,
            },
            mutation=True,
        )
    await data.request("accounts:revoke", {"userId": user_id}, mutation=True)
    revoked = await data.request("accounts:credentials", {"userId": user_id})
    assert revoked["status"] == "revoked" and revoked["ciphertext"] is None
