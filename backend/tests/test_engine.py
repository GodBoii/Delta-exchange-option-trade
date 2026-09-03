from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.delta import DeltaClient
from app.engine import TradingEngine
from app.errors import AppError
from app.models import StrategyDefinition


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

    async def rpc(self, function: str, payload: dict) -> bool:
        assert function == "release_strategy_capital_slot"
        assert payload["p_strategy_id"] == self.strategy["id"]
        return True


class FakeDeltaClient:
    def __init__(self, close_on_order: bool = True) -> None:
        self.close_on_order = close_on_order
        self.position_size = Decimal("-1.0")
        self.placed_orders: list[dict] = []
        self.closed = False

    async def open_orders(self, product_ids: list[int] | None = None) -> dict:
        assert product_ids in (None, [101])
        return {"success": True, "result": []}

    async def positions(self) -> dict:
        return {"success": True, "result": [{"product_id": 101, "size": str(self.position_size)}]}

    async def fills(
        self,
        product_ids: list[int] | None = None,
        start_time: int | None = None,
        after: str | None = None,
    ) -> dict:
        assert product_ids == [101]
        assert start_time is not None
        assert after is None
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


class SettlementDB:
    def __init__(self) -> None:
        self.strategy = {
            "id": "expired-12345678",
            "user_id": "user-1",
            "name": "Expired strategy",
            "status": "attention",
            "entry_execution_at": "2026-08-28T00:00:00Z",
            "exit_execution_at": None,
            "risk_state": {},
            "result_json": {},
        }
        self.executions = [
            {
                "id": "entry-execution",
                "strategy_id": self.strategy["id"],
                "kind": "entry",
                "status": "completed",
                "error": None,
                "started_at": "2026-08-28T00:00:00Z",
                "completed_at": "2026-08-28T00:00:01Z",
            }
        ]
        self.orders = [
            {
                "id": "entry-order",
                "execution_id": "entry-execution",
                "leg_id": "leg-1",
                "delta_order_id": "1001",
                "client_order_id": "entry-client",
                "product_id": 101,
                "product_symbol": "C-BTC-65000-280826",
                "side": "sell",
                "size": 1,
                "filled_size": "1",
                "average_fill_price": "100",
                "commission": "0",
                "contract_value": "0.001",
                "state": "closed",
                "created_at": "2026-08-28T00:00:01Z",
            }
        ]
        self.release_count = 0

    async def select(self, table: str, query: dict) -> list[dict]:
        if table == "strategies":
            return [deepcopy(self.strategy)]
        if table == "executions":
            rows = self.executions
            if query.get("kind") == "eq.entry":
                rows = [row for row in rows if row["kind"] == "entry"]
            return deepcopy(rows)
        if table == "execution_orders":
            if query.get("client_order_id"):
                ids = str(query["client_order_id"]).removeprefix("in.(").removesuffix(")").split(",")
                return [
                    {"client_order_id": row["client_order_id"], "execution_id": row["execution_id"]}
                    for row in self.orders
                    if row["client_order_id"] in ids
                ]
            execution_filter = str(query.get("execution_id") or "")
            ids = execution_filter.removeprefix("in.(").removesuffix(")").split(",")
            return deepcopy([row for row in self.orders if row["execution_id"] in ids])
        raise AssertionError(f"Unexpected select: {table} {query}")

    async def insert(self, table: str, value: dict) -> list[dict]:
        if table == "executions":
            row = {
                "id": f"exit-execution-{len(self.executions)}",
                "started_at": "2026-08-28T12:00:00Z",
                "completed_at": None,
                "error": None,
                **deepcopy(value),
            }
            self.executions.append(row)
            return [deepcopy(row)]
        if table == "execution_orders":
            row = {
                "id": f"settlement-order-{len(self.orders)}",
                "created_at": "2026-08-28T12:00:00Z",
                **deepcopy(value),
            }
            self.orders.append(row)
            return [deepcopy(row)]
        raise AssertionError(f"Unexpected insert: {table} {value}")

    async def update(self, table: str, value: dict, query: dict) -> list[dict]:
        if table == "strategies":
            self.strategy.update(deepcopy(value))
            return [{"id": self.strategy["id"]}]
        if table == "executions":
            execution_id = str(query["id"]).removeprefix("eq.")
            for execution in self.executions:
                if execution["id"] == execution_id:
                    execution.update(deepcopy(value))
                    return [{"id": execution_id}]
            return []
        if table == "execution_orders":
            return [{"id": "entry-order"}]
        raise AssertionError(f"Unexpected update: {table} {value}")

    async def rpc(self, function: str, payload: dict) -> bool:
        assert function == "release_strategy_capital_slot"
        self.release_count += 1
        return True


class ExpiredDeltaClient:
    def __init__(
        self,
        settlement: bool = True,
        settlement_size: str = "1",
        settlement_commission: str = "0",
    ) -> None:
        self.settlement = settlement
        self.settlement_size = settlement_size
        self.settlement_commission = settlement_commission
        self.placed_orders: list[dict] = []

    async def positions(self) -> dict:
        return {"success": True, "result": []}

    async def open_orders(self, product_ids: list[int] | None = None) -> dict:
        return {"success": True, "result": []}

    async def position(self, product_id: int) -> dict:
        raise AppError(400, "expired contract", "expired_contract")

    async def fills(
        self,
        product_ids: list[int] | None = None,
        start_time: int | None = None,
        after: str | None = None,
    ) -> dict:
        if product_ids:
            raise AppError(400, "expired contract", "expired_contract")
        result = [
            {
                "id": 1,
                "order_id": "1001",
                "fill_type": "normal",
                "product_id": 101,
                "product_symbol": "C-BTC-65000-280826",
                "side": "sell",
                "size": "1",
                "price": "100",
                "commission": "0",
                "created_at": "2026-08-28T00:00:01Z",
            }
        ]
        if self.settlement:
            result.append(
                {
                    "id": 2,
                    "order_id": "settlement-uuid",
                    "fill_type": "settlement",
                    "product_id": 101,
                    "product_symbol": "C-BTC-65000-280826",
                    "side": "buy",
                    "size": self.settlement_size,
                    "price": "200",
                    "commission": self.settlement_commission,
                    "created_at": "2026-08-28T12:00:00Z",
                }
            )
        return {"success": True, "result": result, "meta": {"after": None}}

    async def place_order(self, order: dict) -> dict:
        self.placed_orders.append(deepcopy(order))
        raise AssertionError("An expired contract must never receive a close order")

    async def close(self) -> None:
        return None


def settings(**overrides):
    values = {
        "max_entry_lateness_seconds": 60,
        "exit_verify_timeout_seconds": 0,
        "exit_verify_poll_seconds": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_same_name_creates_independent_strategy_runs() -> None:
    class SaveDB:
        def __init__(self) -> None:
            self.strategies: list[dict] = []

        async def select(self, table: str, query: dict) -> list[dict]:
            assert table == "saved_strategies"
            return (
                [{"id": "saved-1"}]
                if query["id"] == "eq.saved-1" and query["or"] == "(user_id.eq.user-1,user_id.is.null)"
                else []
            )

        async def insert(self, table: str, value: dict) -> list[dict]:
            assert table == "strategies"
            row = {"id": f"strategy-{len(self.strategies) + 1}", **deepcopy(value)}
            self.strategies.append(row)
            return [deepcopy(row)]

    entry_at = datetime.now(UTC) + timedelta(hours=1)
    definition = StrategyDefinition.model_validate(
        {
            "name": "BTC ATM short straddle",
            "instrument": {"index": "BTCUSD", "underlying": "BTC", "underlyingFrom": "cash"},
            "entry": {
                "strategyType": "intraday",
                "entryAt": entry_at.isoformat(),
                "exitAt": (entry_at + timedelta(hours=7)).isoformat(),
            },
            "squareOff": "complete",
            "riskMode": "combined_premium",
            "combinedStopLossPercent": 100,
            "legs": [
                {
                    "id": "call-leg",
                    "lots": 1,
                    "position": "sell",
                    "optionType": "call",
                    "expiry": (entry_at + timedelta(days=1)).date().isoformat(),
                    "strikeMode": "atm",
                    "strikeSteps": 0,
                    "orderType": "market_order",
                    "reentryOnTarget": 0,
                    "reentryOnStop": 0,
                },
                {
                    "id": "put-leg",
                    "lots": 1,
                    "position": "sell",
                    "optionType": "put",
                    "expiry": (entry_at + timedelta(days=1)).date().isoformat(),
                    "strikeMode": "atm",
                    "strikeSteps": 0,
                    "orderType": "market_order",
                    "reentryOnTarget": 0,
                    "reentryOnStop": 0,
                },
            ],
            "acknowledgement": True,
        }
    )
    db = SaveDB()
    engine = TradingEngine(db, settings())  # type: ignore[arg-type]

    first = await engine.save_strategy("user-1", definition, "scheduled")
    second = await engine.save_strategy("user-1", definition, "scheduled")

    assert first["id"] != second["id"]
    assert [row["name"] for row in db.strategies] == [definition.name, definition.name]
    assert [row["status"] for row in db.strategies] == ["scheduled", "scheduled"]

    linked = await engine.save_strategy("user-1", definition, "scheduled", "saved-1")
    assert linked["id"] == "strategy-3"
    assert db.strategies[-1]["saved_strategy_id"] == "saved-1"

    with pytest.raises(AppError) as missing:
        await engine.save_strategy("user-1", definition, "scheduled", "saved-by-another-user")
    assert missing.value.code == "saved_strategy_not_found"


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
async def test_expired_flat_strategy_records_settlement_and_releases_capital() -> None:
    db = SettlementDB()
    delta = ExpiredDeltaClient(settlement=True)
    engine = TradingEngine(db, settings())

    result = await engine.reconcile_run_if_flat(db.strategy, delta)  # type: ignore[arg-type]

    assert result is not None
    assert result["closureReason"] == "exchange_settlement"
    assert db.strategy["status"] == "completed"
    assert db.strategy["risk_state"]["exposureStatus"] == "flat"
    assert db.strategy["result_json"]["realizedPnl"] == "-0.100"
    assert db.strategy["exit_execution_at"] == "2026-08-28T12:00:00Z"
    assert db.release_count == 1
    assert delta.placed_orders == []
    assert len([order for order in db.orders if order["leg_id"] == "settlement"]) == 1


@pytest.mark.asyncio
async def test_expiry_reconciliation_is_idempotent() -> None:
    db = SettlementDB()
    delta = ExpiredDeltaClient(settlement=True)
    engine = TradingEngine(db, settings())

    await engine.reconcile_run_if_flat(db.strategy, delta)  # type: ignore[arg-type]
    await engine.reconcile_run_if_flat(db.strategy, delta)  # type: ignore[arg-type]

    assert len([execution for execution in db.executions if execution["kind"] == "exit"]) == 1
    assert len([order for order in db.orders if order["leg_id"] == "settlement"]) == 1
    assert db.strategy["result_json"]["realizedPnl"] == "-0.100"


@pytest.mark.asyncio
async def test_settlement_fill_is_capped_to_the_strategy_owned_size() -> None:
    db = SettlementDB()
    delta = ExpiredDeltaClient(settlement=True, settlement_size="2", settlement_commission="2")
    engine = TradingEngine(db, settings())

    await engine.reconcile_run_if_flat(db.strategy, delta)  # type: ignore[arg-type]

    settlement_order = next(order for order in db.orders if order["leg_id"] == "settlement")
    assert settlement_order["size"] == 1
    assert settlement_order["commission"] == "1"
    assert db.strategy["result_json"]["realizedPnl"] == "-1.100"


@pytest.mark.asyncio
async def test_confirmed_flat_strategy_releases_slot_when_settlement_is_unavailable() -> None:
    db = SettlementDB()
    delta = ExpiredDeltaClient(settlement=False)
    engine = TradingEngine(db, settings())

    result = await engine.reconcile_run_if_flat(db.strategy, delta)  # type: ignore[arg-type]

    assert result is not None
    assert result["closureReason"] == "exchange_flat"
    assert db.strategy["status"] == "attention"
    assert db.strategy["risk_state"]["exposureStatus"] == "flat"
    assert db.release_count == 1
    assert delta.placed_orders == []


@pytest.mark.asyncio
async def test_expired_product_catalogue_recovers_historical_contract_value() -> None:
    class ProductClient:
        product_list_calls = 0

        async def product(self, symbol: str) -> dict:
            raise AppError(400, "expired contract", "expired_contract")

        async def products(self, query: dict) -> dict:
            self.product_list_calls += 1
            assert query["states"] == "expired,settled"
            return {
                "success": True,
                "result": [{"symbol": "C-BTC-65000-280826", "contract_value": "0.001"}],
                "meta": {"after": None},
            }

    client = ProductClient()
    engine = TradingEngine(SettlementDB(), settings())  # type: ignore[arg-type]

    first = await engine.contract_value(client, "C-BTC-65000-280826")  # type: ignore[arg-type]
    second = await engine.contract_value(client, "C-BTC-65000-280826")  # type: ignore[arg-type]

    assert first == Decimal("0.001")
    assert second == first
    assert client.product_list_calls == 1


@pytest.mark.asyncio
async def test_exchange_snapshot_failure_never_releases_capital() -> None:
    class UnavailableDeltaClient(ExpiredDeltaClient):
        async def positions(self) -> dict:
            raise AppError(502, "Delta Exchange is unreachable", "delta_unreachable")

    db = SettlementDB()
    delta = UnavailableDeltaClient()
    engine = TradingEngine(db, settings())

    with pytest.raises(AppError) as raised:
        await engine.reconcile_run_if_flat(db.strategy, delta)  # type: ignore[arg-type]

    assert raised.value.code == "delta_unreachable"
    assert db.strategy["status"] == "attention"
    assert db.strategy["risk_state"] == {}
    assert db.release_count == 0


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
    assert captured["query"]["page_size"] == 50
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


@pytest.mark.asyncio
async def test_terminal_sizing_failure_keeps_real_reason_and_stops_retries() -> None:
    class SchedulerDB:
        def __init__(self) -> None:
            self.strategy = {
                "id": "scheduled-1",
                "status": "scheduled",
                "entry_at": (datetime.now(UTC) - timedelta(seconds=2)).isoformat(),
                "entry_execution_at": None,
                "last_error": None,
            }
            self.proposal_status: dict = {}

        async def select(self, table: str, query: dict) -> list[dict]:
            if table == "strategy_proposals":
                return []
            assert table == "strategies"
            if query.get("status") == "eq.scheduled" and self.strategy["status"] == "scheduled":
                return [{"id": self.strategy["id"], "entry_at": self.strategy["entry_at"]}]
            return []

        async def update(self, table: str, value: dict, query: dict) -> list[dict]:
            if table == "strategies":
                if self.strategy["status"] != "scheduled":
                    return []
                self.strategy.update(deepcopy(value))
                return [{"id": self.strategy["id"]}]
            if table == "strategy_proposals":
                self.proposal_status.update(deepcopy(value))
                return [{"id": "proposal-1"}]
            raise AssertionError(f"Unexpected update: {table}")

    db = SchedulerDB()
    engine = TradingEngine(db, settings(max_entry_lateness_seconds=180))  # type: ignore[arg-type]
    attempts = 0

    async def no_active_risks() -> None:
        return None

    async def reject_for_budget(_strategy_id: str) -> dict:
        nonlocal attempts
        attempts += 1
        raise AppError(409, "One lot does not fit inside the account capital budget", "automatic_lot_too_large")

    engine.process_active_risks = no_active_risks  # type: ignore[method-assign]
    engine.execute_entry = reject_for_budget  # type: ignore[method-assign]

    await engine.process_due_strategies()
    await engine.process_due_strategies()

    assert attempts == 1
    assert db.strategy["status"] == "attention"
    assert db.strategy["last_error"] == "Entry not placed: One lot does not fit inside the account capital budget"
    assert db.proposal_status["status"] == "rejected"


@pytest.mark.asyncio
async def test_activation_recheck_states_are_bound_to_their_proposals() -> None:
    class Database:
        async def select(self, table: str, _query: dict) -> list[dict]:
            if table == "strategy_proposals":
                return [
                    {"id": "proposal-go", "strategy_id": "strategy-go"},
                    {"id": "proposal-drop", "strategy_id": "strategy-drop"},
                    {"id": "proposal-pending", "strategy_id": "strategy-pending"},
                ]
            if table == "automation_agent_runs":
                return [
                    {
                        "strategy_proposal_id": "proposal-go",
                        "status": "completed",
                        "outcome": "strategy_reconfirmed",
                    },
                    {
                        "strategy_proposal_id": "proposal-drop",
                        "status": "completed",
                        "outcome": "strategy_dropped",
                    },
                    {"strategy_proposal_id": "proposal-pending", "status": "running", "outcome": None},
                ]
            raise AssertionError(f"Unexpected select: {table}")

    engine = TradingEngine(Database(), settings())  # type: ignore[arg-type]

    states = await engine.activation_recheck_states(
        ["strategy-go", "strategy-drop", "strategy-pending", "manual-strategy"]
    )

    assert states == {
        "strategy-go": "ready",
        "strategy-drop": "dropped",
        "strategy-pending": "pending",
        "manual-strategy": "ready",
    }
