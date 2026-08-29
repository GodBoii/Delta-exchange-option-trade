import json
from types import SimpleNamespace

import httpx

from app.supabase import SupabaseAdmin


async def test_publishes_strategy_invalidation_without_moving_strategy_data() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success", "value": "signal-id"})

    settings = SimpleNamespace(
        supabase_url="https://supabase.example",
        supabase_service_role_key="service-key",
        supabase_publishable_key="publishable-key",
        convex_url="https://convex.example",
        convex_sync_secret="sync-secret",
    )
    database = SupabaseAdmin(settings)  # type: ignore[arg-type]
    await database.client.aclose()
    database.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await database._publish_signal(
            "strategies",
            {
                "id": "strategy-1",
                "user_id": "user-1",
                "status": "active",
                "definition_json": {"private": True},
            },
        )
    finally:
        await database.close()

    assert requests == [{
        "path": "signals:publish",
        "format": "json",
        "args": {
            "syncSecret": "sync-secret",
            "userId": "user-1",
            "scope": "strategies",
            "entityId": "strategy-1",
            "status": "active",
        },
    }]
