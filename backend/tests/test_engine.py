from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.delta import DeltaClient
from app.engine import TradingEngine
from app.errors import AppError


class FakeDB:
    def __init__(self) -> None:
        self.strategy = {
            "id": "strategy-12345678",
            "user_id": "user-1",
            "name": "Verified exit",
            "status": "active",
            "definition_json": {},
            "entry_execution_at": "2026-08-08T10:00:00Z",
            "exit_execution_at": None,
            "combined_stop_triggered_at": None,
            "risk_state": {},
        }
        self.entry_order = {
            "id": "entry-order-row",
            "leg_id": "leg-1",
            "delta_order_id": "1001",
            "client_order_id": "entry-client-1",
            "product_id": 101,
            "product_symbol": "C-BTC-65000-090826",
            "side": "sell",
            "size": 1,
            "state": "closed",
            "filled_size": "1.0",
            "average_fill_price": "100",
            "commission": "0",
            "created_at": "2026-08-08T10:00:00Z",
        }
        self.exit_orders: list[dict] = []
        self.execution_updates: list[dict] = []

    async def select(self, table: str, query: dict) -> list[dict]:
        if table == "strategies":
            return [deepcopy(self.strategy)]
        if table == "executions":
            return [{"id": "entry-execution"}]
        if table == "execution_orders":
            return [deepcopy(self.entry_order)]
        raise AssertionError(f"Unexpected select: {table} {query}")

    async def insert(self, table: str, value: dict) -> list[dict]:
        if table == "executions":
            return [{"id": "exit-execution", **value}]
        if table == "execution_orders":
            self.exit_orders.append(deepcopy(value))
            return [{"id": "exit-order-row", **value}]
        raise AssertionError(f"Unexpected insert: {table} {value}")

    async def update(self, table: str, value: dict, query: dict) -> list[dict]:
        if table == "strategies":
            allowed = str(query.get("status", ""))
            if allowed and self.strategy["status"] not in allowed:
                return []
            self.strategy.update(deepcopy(value))
            return [{"id": self.strategy["id"]}]
        if table == "executions":
            self.execution_updates.append(deepcopy(value))
            return [{"id": "exit-execution"}]
        if table == "execution_orders":
            for order in self.exit_orders:
                if order["client_order_id"] == str(query["client_order_id"]).removeprefix("eq."):
                    order.update(deepcopy(value))
            return [{"id": "exit-order-row"}]
        raise AssertionError(f"Unexpected update: {table} {value}")


class FakeDeltaClient:
    def __init__(self, close_on_order: bool = True) -> None:
        self.close_on_order = close_on_order
        self.position_size = Decimal("-1.0")
        self.placed_orders: list[dict] = []
        self.closed = False

    async def open_orders(self, product_ids: list[int] | None = None) -> dict:
        assert product_ids == [101]
        return {"success": True, "result": []}

    async def fills(self, product_ids: list[int], start_time: int | None = None) -> dict:
        assert product_ids == [101]
        assert start_time is not None
        return {
            "success": True,
            "result": [{"order_id": "1001", "size": "1.0", "price": "100", "commission": "0"}],
        }

    async def position(self, product_id: int) -> dict:
        assert product_id == 101
        return {"success": True, "result": {"size": str(self.position_size)}}

    async def place_order(self, order: dict) -> dict:
        self.placed_orders.append(deepcopy(order))
        if self.close_on_order:
            self.position_size = Decimal("0")
        return {
            "success": True,
            "result": {"id": 2002, "state": "closed", "unfilled_size": 0, "average_fill_price": "110"},
        }

    async def close(self) -> None:
        self.closed = True


def settings(**overrides):
    values = {
        "max_entry_lateness_seconds": 60,
        "exit_verify_timeout_seconds": 0,
        "exit_verify_poll_seconds": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_strategy_exit_uses_integer_reduce_only_order_and_verifies_position() -> None:
    db = FakeDB()
    delta = FakeDeltaClient(close_on_order=True)
    engine = TradingEngine(db, settings())

    async def client_for_user(_user_id: str) -> FakeDeltaClient:
        return delta

    engine.client_for_user = client_for_user  # type: ignore[method-assign]
    result = await engine.execute_exit(db.strategy["id"])

    assert result["verified"] is True
    assert result["positionsVerified"] == 1
    assert delta.placed_orders == [
        {
            "product_id": 101,
            "product_symbol": "C-BTC-65000-090826",
            "size": 1,
            "side": "buy",
            "order_type": "market_order",
            "reduce_only": True,
            "client_order_id": delta.placed_orders[0]["client_order_id"],
        }
    ]
    assert isinstance(delta.placed_orders[0]["size"], int)
    assert db.strategy["status"] == "completed"
    assert db.strategy["exit_execution_at"] is not None
    assert db.exit_orders[0]["state"] == "verified_closed"
    assert delta.closed is True


@pytest.mark.asyncio
async def test_unconfirmed_exit_is_attention_and_remains_retryable() -> None:
    db = FakeDB()
    delta = FakeDeltaClient(close_on_order=False)
    engine = TradingEngine(db, settings())

    async def client_for_user(_user_id: str) -> FakeDeltaClient:
        return delta

    engine.client_for_user = client_for_user  # type: ignore[method-assign]
    with pytest.raises(AppError) as raised:
        await engine.execute_exit(db.strategy["id"])

    assert raised.value.code == "exit_not_confirmed"
    assert db.strategy["status"] == "attention"
    assert db.strategy["exit_execution_at"] is None
    assert db.execution_updates[-1]["status"] == "partial_or_failed"
    assert db.exit_orders[0]["state"] == "closed"


@pytest.mark.asyncio
async def test_open_orders_uses_documented_states_filter() -> None:
    client = object.__new__(DeltaClient)
    captured: dict = {}

    async def request(method: str, path: str, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {"success": True, "result": []}

    client.request = request  # type: ignore[method-assign]
    await client.open_orders([101, 202])

    assert captured["query"]["states"] == "open,pending"
    assert captured["query"]["product_ids"] == "101,202"
    assert "state" not in captured["query"]


@pytest.mark.asyncio
async def test_cancel_order_uses_documented_delete_body() -> None:
    client = object.__new__(DeltaClient)
    captured: dict = {}

    async def request(method: str, path: str, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {"success": True, "result": {"state": "cancelled"}}

    client.request = request  # type: ignore[method-assign]
    await client.cancel_order(123, 101)

    assert captured["method"] == "DELETE"
    assert captured["path"] == "/v2/orders"
    assert captured["body"] == {"id": 123, "product_id": 101}
    assert captured["authenticated"] is True


@pytest.mark.asyncio
async def test_scheduled_strategy_can_be_cancelled_before_entry() -> None:
    db = FakeDB()
    db.strategy["status"] = "scheduled"
    db.strategy["entry_execution_at"] = None
    engine = TradingEngine(db, settings())

    await engine.cancel_strategy(db.strategy["id"], db.strategy["user_id"])

    assert db.strategy["status"] == "cancelled"
    assert db.strategy["entry_execution_at"] is None
