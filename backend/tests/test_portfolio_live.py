"""Live portfolio: private-stream state, display-only prices, watcher sessions and the WebSocket."""

import asyncio
import contextlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import engine as engine_module
from app import portfolio_stream
from app.delta_events import DeltaEvents, PublicMarkFeeds
from app.engine import TradingEngine
from app.portfolio_state import AccountPortfolio
from app.portfolio_stream import origin_allowed, serve_portfolio

PRODUCT = {
    "id": 154884, "symbol": "P-BTC-83600-270926", "contract_value": "0.001", "contract_type": "put_options",
    "notional_type": "vanilla", "strike_price": "83600", "settlement_time": "2026-09-27T12:00:00Z",
    "spot_index": {"symbol": ".DEXBTUSD", "constituent_indices": {"expression": "big"}},
    "underlying_asset": {"symbol": "BTC", "networks": []}, "ui_config": {"tags": []},
}
POSITION = {
    "product_id": 154884, "product_symbol": "P-BTC-83600-270926", "size": -17, "entry_price": "126.7",
    "margin": "9.292676", "margin_mode": "cross", "liquidation_price": None, "mark_price": "123.17",
    "realized_cashflow": "2.1539", "realized_pnl": "0", "unrealized_pnl": "2.142", "user_id": 1,
    "product": PRODUCT,
}
STOP_ORDER = {
    "id": 1564243452, "product_id": 154884, "product_symbol": "P-BTC-83600-270926", "side": "buy", "size": 17,
    "unfilled_size": 17, "state": "pending", "order_type": "market_order", "stop_order_type": "stop_loss_order",
    "stop_price": "333.64", "stop_trigger_method": "mark_price", "reduce_only": True, "bracket_order": True,
    "created_at": "2026-09-25T19:25:02Z", "meta_data": {"ip": "203.0.113.9"},
}
WALLET = {"asset_symbol": "USD", "balance": "35.04", "available_balance": "14.92", "blocked_margin": "20.11",
          "cross_position_margin": "18.90", "id": 1}


def ready_portfolio() -> AccountPortfolio:
    portfolio = AccountPortfolio()
    portfolio.apply({"type": "positions", "action": "snapshot", "result": [POSITION]}, 0)
    portfolio.apply({"type": "orders", "action": "snapshot", "result": [STOP_ORDER]}, 0)
    portfolio.seed_balances([WALLET], portfolio.generation)
    return portfolio


def test_snapshot_rows_are_trimmed_to_what_the_view_reads():
    portfolio = ready_portfolio()
    assert portfolio.ready
    snapshot = portfolio.snapshot()
    position = snapshot["positions"][0]
    assert position["product"]["spot_index"] == {"symbol": ".DEXBTUSD"}
    assert position["product"]["contract_value"] == "0.001"
    assert "ui_config" not in position["product"] and "user_id" not in position
    assert "meta_data" not in snapshot["orders"][0]
    assert snapshot["balances"][0]["cross_position_margin"] == "18.90"
    assert "id" not in snapshot["balances"][0]


def test_incremental_position_update_keeps_product_and_schedules_cashflow_refresh():
    portfolio = ready_portfolio()
    version = portfolio.version
    portfolio.apply({"type": "positions", "action": "update", "symbol": "P-BTC-83600-270926",
                     "product_id": 154884, "size": -10, "margin": "5.1"}, 100.0)
    position = portfolio.positions[154884]
    assert position["size"] == -10 and position["margin"] == "5.1"
    assert position["product"]["symbol"] == "P-BTC-83600-270926"
    assert position["realized_cashflow"] == "2.1539"
    assert portfolio.version > version
    assert portfolio.enrich_after == pytest.approx(112.0)

    # A REST row that still shows the old size is stale and must not override the socket.
    portfolio.enrich_positions([{**POSITION, "size": -17, "realized_cashflow": "9"}])
    assert portfolio.positions[154884]["realized_cashflow"] == "2.1539"
    portfolio.enrich_positions([{**POSITION, "size": -10, "realized_cashflow": "1.2", "margin": "99"}])
    assert portfolio.positions[154884]["realized_cashflow"] == "1.2"
    assert portfolio.positions[154884]["margin"] == "5.1"

    portfolio.apply({"type": "positions", "action": "update", "product_id": 154884, "size": 0}, 101.0)
    assert portfolio.positions == {}


def test_new_position_without_product_asks_for_metadata():
    portfolio = ready_portfolio()
    portfolio.apply({"type": "positions", "action": "create", "symbol": "C-BTC-84400-270926",
                     "product_id": 154896, "size": -17, "entry_price": "145.5"}, 0)
    assert portfolio.missing_products() == {"C-BTC-84400-270926"}
    portfolio.set_product("C-BTC-84400-270926", {**PRODUCT, "id": 154896, "symbol": "C-BTC-84400-270926"})
    assert portfolio.missing_products() == set()
    assert portfolio.positions[154896]["product"]["contract_value"] == "0.001"


def test_order_updates_use_order_id_and_drop_finished_orders():
    portfolio = ready_portfolio()
    portfolio.apply({"type": "orders", "action": "create", "order_id": 77, "symbol": "C-BTC-84400-270926",
                     "product_id": 154896, "side": "sell", "size": 5, "unfilled_size": 5, "state": "open",
                     "limit_price": "150", "timestamp": 1_790_375_955_671_006}, 0)
    order = portfolio.orders["77"]
    assert order["product_symbol"] == "C-BTC-84400-270926"
    assert order["created_at"].startswith("2026-09-25T")
    portfolio.apply({"type": "orders", "action": "update", "order_id": 77, "unfilled_size": 2, "state": "open"}, 0)
    assert portfolio.orders["77"]["unfilled_size"] == 2 and portfolio.orders["77"]["limit_price"] == "150"
    portfolio.apply({"type": "orders", "action": "update", "order_id": 77, "state": "closed"}, 0)
    assert "77" not in portfolio.orders
    portfolio.apply({"type": "orders", "action": "delete", "order_id": 1564243452}, 0)
    assert portfolio.orders == {}


def test_pushed_wallet_rows_win_over_a_later_rest_seed_and_resets_drop_stale_seeds():
    portfolio = AccountPortfolio()
    portfolio.apply({"type": "margins", "asset_symbol": "USD", "balance": "40", "available_balance": "20"}, 0)
    generation = portfolio.generation
    portfolio.seed_balances([WALLET], generation)
    assert portfolio.balances["USD"]["balance"] == "40"

    portfolio.reset()
    assert not portfolio.ready and portfolio.balances == {}
    portfolio.seed_balances([WALLET], generation)
    assert not portfolio.balances_ready and portfolio.balances == {}


async def test_waiters_wake_on_change_and_time_out_otherwise():
    portfolio = AccountPortfolio()
    started = time.monotonic()
    await portfolio.wait_for_change(portfolio.version, 0.05)
    assert time.monotonic() - started >= 0.04
    waiting = asyncio.create_task(portfolio.wait_for_change(portfolio.version, 5))
    await asyncio.sleep(0)
    portfolio.apply({"type": "margins", "asset_symbol": "USD", "balance": "1"}, 0)
    await asyncio.wait_for(waiting, 1)
    assert portfolio._waiters == []


def test_display_prices_do_not_wake_the_engine_but_risk_marks_still_do():
    wake = asyncio.Event()
    feed = DeltaEvents("", "", wake, "wss://unused", "wss://unused")
    feed.connected["public"] = True
    timestamp = int(time.time() * 1_000_000)
    assert feed.watch_mark("PUT", 5) is None
    assert feed.spot(".DEXBTUSD", 5) is None
    feed.receive({"type": "mark_price", "sy": "MARK:PUT", "p": "123.4", "ts": timestamp}, "public")
    feed.receive({"type": "spot_price", "sy": ".DEXBTUSD", "p": 84066.1, "ts": timestamp}, "public")
    assert feed.watch_mark("PUT", 5) == "123.4"
    assert feed.spot(".DEXBTUSD", 5) == "84066.1"
    assert not wake.is_set()

    assert feed.mark("PUT", 5) == "123.4"
    feed.receive({"type": "mark_price", "sy": "MARK:PUT", "p": "124", "ts": timestamp + 1}, "public")
    assert wake.is_set()
    assert feed.load() == 2


def test_private_messages_update_state_and_keep_engine_wakeups():
    wake = asyncio.Event()
    feed = DeltaEvents("key", "secret", wake, "wss://unused", "wss://unused")
    feed.receive({"type": "margins", "asset_symbol": "USD", "balance": "1"}, "private")
    assert not wake.is_set()
    feed.receive({"type": "positions", "action": "snapshot", "result": [POSITION]}, "private")
    assert wake.is_set()
    assert 154884 in feed.portfolio.positions


async def test_shared_feeds_keep_index_symbols_apart_from_contracts(monkeypatch):
    monkeypatch.setattr(DeltaEvents, "start", lambda self, kinds=("public", "private"): None)
    marks = PublicMarkFeeds(asyncio.Event(), "wss://unused", "wss://unused")
    marks.watch_mark("PUT", 5)
    marks.spot(".DEXBTUSD", 5)
    assert set(marks.by_symbol) == {"PUT", "spot:.DEXBTUSD"}
    feed = marks.feeds[0]
    assert feed.watched == {"PUT"} and feed.spot_symbols == {".DEXBTUSD"}
    await marks.close()


def engine_settings() -> SimpleNamespace:
    return SimpleNamespace(
                delta_events_enabled=True, delta_production_url="https://api.india.delta.exchange",
        delta_public_ws_url="wss://public.example", delta_private_ws_url="wss://private.example",
        delta_mark_max_age_seconds=5,
    )


async def test_a_watcher_keeps_the_session_but_never_blocks_a_credential_change(monkeypatch):
    keys = {"current": "first"}
    monkeypatch.setattr(engine_module, "credentials_for_user", AsyncMock(side_effect=lambda _db, user_id: {
        "api_key": keys["current"], "api_secret": "secret", "delta_user_id": user_id,
    }))
    monkeypatch.setattr(DeltaEvents, "start", lambda self, kinds=("public", "private"): None)
    engine = TradingEngine(SimpleNamespace(runtime=SimpleNamespace(pool=None)), engine_settings())
    try:
        async with engine.watch_account("owner") as events:
            session = engine.sessions["owner"]
            assert session.leases == 0 and session.watchers == 1
            session.last_used = time.monotonic() - 600
            other = await engine.client_for_user("someone-else")
            await other.close()
            assert "owner" in engine.sessions, "an idle sweep must not close a watched session"

            keys["current"] = "rotated"
            # Reconnecting an account invalidates its cached credentials.
            await engine.invalidate_credentials("owner")
            client = await engine.client_for_user("owner")
            await client.close()
            assert events.closed, "the replaced session ends the live view"
            assert engine.sessions["owner"] is not session
        assert session.watchers == 0
    finally:
        await engine.close()


async def test_watch_requires_live_events(monkeypatch):
    monkeypatch.setattr(engine_module, "credentials_for_user", AsyncMock(return_value={
        "api_key": "k", "api_secret": "s", "delta_user_id": "1",
    }))
    settings = SimpleNamespace(**{**vars(engine_settings()), "delta_events_enabled": False})
    engine = TradingEngine(SimpleNamespace(runtime=SimpleNamespace(pool=None)), settings)
    try:
        with pytest.raises(engine_module.AppError) as error:
            async with engine.watch_account("owner"):
                pass
        assert error.value.code == "live_events_unavailable"
        assert engine.sessions["owner"].leases == 0
    finally:
        await engine.close()


def test_origin_policy_matches_the_http_cors_policy():
    settings = SimpleNamespace(allowed_origins=["https://tradecognition.online"],
                               frontend_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")
    assert origin_allowed("https://tradecognition.online", settings)
    assert origin_allowed("http://localhost:3000", settings)
    assert origin_allowed(None, settings)
    assert not origin_allowed("https://evil.example", settings)


class FakeClient:
    def __init__(self) -> None:
        self.balances = AsyncMock(return_value={"result": [WALLET]})
        self.positions = AsyncMock(return_value={"result": [POSITION]})

    async def close(self) -> None:
        return None


class FakeEngine:
    def __init__(self, events: DeltaEvents) -> None:
        self.settings = SimpleNamespace(delta_events_enabled=True)
        self.events = events
        self.client = FakeClient()
        self.watching = 0

    async def client_for_user(self, _user_id: str) -> FakeClient:
        return self.client

    @contextlib.asynccontextmanager
    async def watch_account(self, _user_id: str):
        self.watching += 1
        try:
            yield self.events
        finally:
            self.watching -= 1


def fake_db(token: str = "good") -> SimpleNamespace:
    async def auth_user(value: str):
        return {"id": "owner", "email": "owner@example.com"} if value == token else None

    async def request(path: str, _args: dict, *, mutation: bool = False):
        assert path == "accounts:overview"
        return {"connection": {"id": "c", "delta_user_id": "57709647", "account_name": "Main", "status": "connected"}}

    async def profile(_user_id: str) -> dict:
        return {}

    return SimpleNamespace(
        auth_user=auth_user,
        profile=profile,
        credential_store=SimpleNamespace(data=SimpleNamespace(request=request)),
    )


def stream_app(db: SimpleNamespace, engine: FakeEngine) -> FastAPI:
    app = FastAPI()
    settings = SimpleNamespace(allowed_origins=["https://tradecognition.online"], frontend_origin_regex=r"^$")

    @app.websocket("/ws/portfolio")
    async def route(websocket: WebSocket) -> None:
        await serve_portfolio(websocket, db, engine, settings)

    return app


def receive_until(socket, kind: str, limit: int = 20) -> dict:
    for _ in range(limit):
        message = socket.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} message")


def test_stream_pushes_state_and_live_prices(monkeypatch):
    monkeypatch.setattr(portfolio_stream, "TICK_SECONDS", 0.02)
    events = DeltaEvents("key", "secret", asyncio.Event(), "wss://unused", "wss://unused")
    events.connected.update(public=True, private=True)
    events.receive({"type": "positions", "action": "snapshot", "result": [POSITION]}, "private")
    events.receive({"type": "orders", "action": "snapshot", "result": [STOP_ORDER]}, "private")
    engine = FakeEngine(events)
    with TestClient(stream_app(fake_db(), engine)) as client, client.websocket_connect(
        "/ws/portfolio", headers={"origin": "https://tradecognition.online"}
    ) as socket:
        socket.send_json({"type": "auth", "token": "good"})
        state = receive_until(socket, "state")
        assert state["positions"][0]["product_symbol"] == "P-BTC-83600-270926"
        assert state["orders"][0]["stop_order_type"] == "stop_loss_order"
        assert state["balances"][0]["asset_symbol"] == "USD"
        assert engine.client.balances.await_count == 1

        timestamp = int(time.time() * 1_000_000)
        # Feed events on the server's loop thread, as the Delta socket task would.
        socket.portal.call(events.receive, {
            "type": "mark_price", "sy": "MARK:P-BTC-83600-270926", "p": "130.5", "ts": timestamp,
        }, "public")
        socket.portal.call(events.receive, {"type": "spot_price", "sy": ".DEXBTUSD", "p": 84000, "ts": timestamp},
                           "public")
        prices = receive_until(socket, "prices")
        while prices["marks"] == {} or prices["indices"] == {}:
            prices = receive_until(socket, "prices")
        assert prices["marks"] == {"P-BTC-83600-270926": "130.5"}
        assert prices["indices"] == {".DEXBTUSD": "84000"}

        # End from the server side so the test client never cancels a running handler.
        events.closed = True
        with pytest.raises(WebSocketDisconnect):
            for _ in range(50):
                socket.receive_json()
    assert engine.watching == 0


def test_stream_rejects_bad_tokens_and_foreign_origins():
    events = DeltaEvents("key", "secret", asyncio.Event(), "wss://unused", "wss://unused")
    engine = FakeEngine(events)
    with TestClient(stream_app(fake_db(), engine)) as client:
        with client.websocket_connect("/ws/portfolio") as socket:
            socket.send_json({"type": "auth", "token": "wrong"})
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 4401
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect(
            "/ws/portfolio", headers={"origin": "https://evil.example"}
        ):
            pass
        assert refused.value.code == 1008
    assert engine.watching == 0


def test_stream_closes_when_the_account_session_is_replaced(monkeypatch):
    monkeypatch.setattr(portfolio_stream, "TICK_SECONDS", 0.02)
    events = DeltaEvents("key", "secret", asyncio.Event(), "wss://unused", "wss://unused")
    engine = FakeEngine(events)
    with TestClient(stream_app(fake_db(), engine)) as client, client.websocket_connect("/ws/portfolio") as socket:
        socket.send_json({"type": "auth", "token": "good"})
        assert receive_until(socket, "status")["private"] == "syncing"
        events.closed = True
        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(50):
                socket.receive_json()
        assert closed.value.code == 4001
