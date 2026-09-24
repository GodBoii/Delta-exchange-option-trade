from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import engine as engine_module
from app.delta_events import DeltaEvents
from app.engine import TradingEngine


async def test_account_sessions_share_http_pool_without_sharing_signing_keys(monkeypatch):
    credentials = AsyncMock(side_effect=lambda _db, user_id: {
        "api_key": f"key-{user_id}", "api_secret": f"secret-{user_id}", "delta_user_id": user_id,
    })
    monkeypatch.setattr(engine_module, "credentials_for_user", credentials)
    settings = SimpleNamespace(
        convex_library_enabled=False, convex_order_journal_enabled=False,
        delta_events_enabled=False, delta_production_url="https://api.india.delta.exchange",
        delta_mark_max_age_seconds=5,
    )
    engine = TradingEngine(SimpleNamespace(), settings)
    clients = [await engine.client_for_user(f"user-{index}") for index in range(110)]
    try:
        assert len(engine.sessions) == 110
        assert len({id(client.client) for client in clients}) == 1
        assert len({client.api_key for client in clients}) == 110
        assert len({id(client.budget) for client in clients}) == 110
    finally:
        for client in clients:
            await client.close()
        await engine.close()
    assert engine.exchange_http.is_closed


async def test_account_private_streams_share_one_public_mark_source(monkeypatch):
    monkeypatch.setattr(engine_module, "credentials_for_user", AsyncMock(
        side_effect=lambda _db, user_id: {
            "api_key": user_id, "api_secret": user_id, "delta_user_id": user_id,
        }
    ))
    started = []
    monkeypatch.setattr(DeltaEvents, "start", lambda _self, kinds=("public", "private"): started.append(kinds))
    settings = SimpleNamespace(
        convex_library_enabled=False, convex_order_journal_enabled=False,
        delta_events_enabled=True, delta_production_url="https://api.india.delta.exchange",
        delta_public_ws_url="wss://public.example", delta_private_ws_url="wss://private.example",
        delta_mark_max_age_seconds=5,
    )
    engine = TradingEngine(SimpleNamespace(), settings)
    first = await engine.client_for_user("one")
    second = await engine.client_for_user("two")
    try:
        assert first.events is not second.events
        assert first.events.shared_marks is second.events.shared_marks is engine.public_marks
        assert started == [("private",), ("private",)]
        assert first.events.mark("CALL", 5) is None
        assert second.events.mark("CALL", 5) is None
        assert started == [("private",), ("private",), ("public",)]
    finally:
        await first.close()
        await second.close()
        await engine.close()
