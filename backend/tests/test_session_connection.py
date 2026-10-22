"""Session responses and non-mutating connection checks without a live database."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import auth
from app.errors import AppError


@pytest.fixture
def session_api(monkeypatch):
    from app import main

    user = {"id": "user-a", "session_id": "session-a", "email": "private@example.test"}
    data = SimpleNamespace(request=AsyncMock(return_value={"connection": None}))
    db = SimpleNamespace(
        auth_user=AsyncMock(side_effect=lambda token: user if token == "valid" else None),
        profile=AsyncMock(return_value={"user_type": "user"}),
        credential_store=SimpleNamespace(data=data),
    )
    monkeypatch.setattr(main.app.state, "db", db, raising=False)
    return main.app, db


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer expired"}])
async def test_invalid_application_auth_is_not_a_disconnected_delta_session(session_api, headers):
    app, db = session_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/session", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    db.credential_store.data.request.assert_not_awaited()


@pytest.mark.parametrize("status,connected", [("connected", True), ("revoked", False)])
async def test_session_reports_persisted_connection_status(session_api, status, connected):
    app, db = session_api
    db.credential_store.data.request.return_value = {"connection": {
        "id": "connection-a", "status": status, "delta_user_id": "123", "account_name": "Main",
    }}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/session", headers={"Authorization": "Bearer valid"})
    result = response.json()
    assert response.status_code == 200 and result["authenticated"]
    assert result["connected"] is connected
    assert (result["account"] is not None) is connected
    assert result["user"]["id"] == "user-a"
    db.credential_store.data.request.assert_awaited_once_with("accounts:overview", {"userId": "user-a"})


async def test_signed_in_user_without_delta_keeps_the_application_identity(session_api):
    app, _ = session_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        result = (await client.get("/api/session", headers={"Authorization": "Bearer valid"})).json()
    assert result["authenticated"] and not result["connected"] and result["account"] is None


async def test_session_events_log_verified_identity_without_tokens_or_email(session_api, caplog):
    app, _ = session_api
    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/session/events", json={"event": "sign_out_local"},
                                     headers={"Authorization": "Bearer valid"})
        invalid = await client.post("/api/session/events", json={"event": "signed_in", "token": "secret"},
                                    headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200 and invalid.status_code == 400
    assert "event=sign_out_local user_id=user-a session_id=session-a" in caplog.text
    assert "Bearer valid" not in caplog.text and "private@example.test" not in caplog.text


@pytest.mark.parametrize("failed_probe", ["balances", "positions", "open_orders"])
async def test_connection_probe_failure_preserves_the_previous_credential(monkeypatch, failed_probe):
    client = SimpleNamespace(**{name: AsyncMock(return_value={"result": []})
                               for name in ("balances", "positions", "open_orders")},
                             profile=AsyncMock(return_value={"result": {"id": 123}}), close=AsyncMock())
    getattr(client, failed_probe).side_effect = AppError(403, "Access denied", "permission_denied")
    monkeypatch.setattr(auth, "DeltaClient", lambda *_: client)
    store = SimpleNamespace(save=AsyncMock(), data=SimpleNamespace(request=AsyncMock()))
    with pytest.raises(AppError, match="Access denied"):
        await auth.create_connection(SimpleNamespace(credential_store=store), object(), "user-a", "key", "secret")
    store.save.assert_not_awaited()
    store.data.request.assert_not_awaited()
    client.close.assert_awaited_once()


async def test_main_connection_checks_read_access_and_never_places_a_trade(monkeypatch):
    client = SimpleNamespace(**{name: AsyncMock(return_value={"result": []})
                               for name in ("balances", "positions", "open_orders")},
                             profile=AsyncMock(return_value={"result": {"id": 123}}), close=AsyncMock())
    monkeypatch.setattr(auth, "DeltaClient", lambda *_: client)
    store = SimpleNamespace(save=AsyncMock(return_value="connection-a"),
                            data=SimpleNamespace(request=AsyncMock(return_value={"connection": None})))
    result = await auth.create_connection(SimpleNamespace(credential_store=store), object(), "user-a", "key", "secret")
    assert result["account"]["id"] == "123"
    for name in ("profile", "balances", "positions", "open_orders", "close"):
        getattr(client, name).assert_awaited_once()
    store.save.assert_awaited_once()
