"""Authenticated local library and research endpoints respect the recovery gate."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from psycopg_pool import AsyncConnectionPool

from app.local_application_data import LocalApplicationData

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


@pytest.fixture(scope="module")
def event_loop_policy():
    if os.name == "nt":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.mark.asyncio
async def test_local_library_and_private_research_endpoint(monkeypatch):
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_URL", "https://supabase.test")
    monkeypatch.setenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    from app import main

    user_id, identifier = str(uuid4()), str(uuid4())
    async with AsyncConnectionPool(os.environ["TEST_LOCAL_DATABASE_URL"], open=False) as pool:
        await pool.open()
        main.app.state.db = SimpleNamespace(local_data=LocalApplicationData(pool))
        main.app.state.recovery_pending = False
        monkeypatch.setattr(main, "settings", SimpleNamespace(
            application_storage="local", trading_writer_enabled=True, analysis_service_secret="research-key"
        ))
        main.app.dependency_overrides[main.require_user] = lambda: {"id": user_id}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test.local"
            ) as client:
                body = {"id": identifier, "name": "Saved", "definitionJson":
                        '{"name":"Saved","enabledForAi":false,"legs":[{}]}',
                        "enabled": False, "expectedVersion": None}
                saved = await client.put(f"/api/library/{identifier}", json=body)
                assert saved.status_code == 200
                assert saved.json()["result"]["version"] == 1
                listed = await client.get("/api/library")
                assert any(item["id"] == identifier for item in listed.json()["result"])
                rejected = await client.post("/internal/research", json={
                    "path": "accounts:overview", "args": {"userId": user_id},
                })
                assert rejected.status_code == 401
                allowed = await client.post("/internal/research", headers={
                    "X-Analysis-Secret": "research-key",
                }, json={"path": "accounts:overview", "args": {"userId": user_id}})
                assert allowed.status_code == 200
                main.app.state.recovery_pending = True
                assert (await client.delete(f"/api/library/{identifier}?expectedVersion=1")).status_code == 503
        finally:
            main.app.dependency_overrides.clear()
