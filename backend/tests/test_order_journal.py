import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine import TradingEngine
from app.errors import AppError, DeltaOrderRejected
from app.order_journal import OrderJournal


class MemoryJournal(OrderJournal):
    """Dispatch rules under test; storage is a dictionary."""

    def __init__(self, account_id: str = "account") -> None:
        self.account_id = account_id
        self.records: dict[str, dict] = {}

    async def call(self, path, args, *, query=False):
        key = args["clientOrderId"]
        if path == "orderIntents:begin":
            existing = self.records.get(key)
            if existing is None:
                self.records[key] = {"kind": "unknown"}
            return {"dispatch": existing is None, "outcome": deepcopy(self.records[key])}
        self.records[key] = args["outcome"]
        return None


class UnavailableJournal(OrderJournal):
    account_id = "account"

    async def call(self, path, args, *, query=False):
        raise AppError(503, "Trading journal unavailable; order outcome must be checked", "journal_unavailable")


@pytest.fixture
def journal():
    return MemoryJournal()


async def test_lost_exchange_response_uses_lookup_without_resubmission(journal):
    send = AsyncMock(side_effect=AppError(502, "Disconnected", "delta_unreachable"))
    lookup = AsyncMock(return_value={"result": {"id": 42}})
    payload = {"client_order_id": "original", "size": 1}
    with pytest.raises(AppError):
        await journal.submit(payload, send, lookup)
    assert await journal.submit(payload, send, lookup) == {"result": {"id": 42}}
    assert await journal.submit(payload, send, lookup) == {"result": {"id": 42}}
    send.assert_awaited_once()
    lookup.assert_awaited_once_with("original")


async def test_not_found_does_not_authorize_another_order(journal):
    send = AsyncMock(side_effect=AppError(502, "Disconnected"))
    lookup = AsyncMock(side_effect=AppError(404, "Not found"))
    for _ in range(3):
        with pytest.raises(AppError):
            await journal.submit({"client_order_id": "original"}, send, lookup)
    send.assert_awaited_once()


async def test_explicit_rejection_is_retained(journal):
    send = AsyncMock(side_effect=DeltaOrderRejected(400, "No liquidity", "no_liquidity"))
    lookup = AsyncMock()
    for _ in range(2):
        with pytest.raises(DeltaOrderRejected):
            await journal.submit({"client_order_id": "original"}, send, lookup)
    send.assert_awaited_once()
    lookup.assert_not_awaited()


async def test_journal_outage_blocks_dispatch():
    send = AsyncMock()
    with pytest.raises(AppError, match="journal unavailable"):
        await UnavailableJournal().submit({"client_order_id": "original"}, send, AsyncMock())
    send.assert_not_awaited()


@pytest.mark.parametrize("lookup_fails", [False, True])
async def test_restart_recovers_order_record_or_preserves_unknown_outcome(lookup_fails):
    context = {"strategyId": "run", "executionId": "execution", "legId": "call", "contractValue": ".001"}
    intent = {
        "_creationTime": 1_789_344_001_000,
        "context": context,
        "clientOrderId": "same-order",
        "outcome": {"kind": "unknown"},
        "materialized": False,
        "payload": json.dumps(
            {"product_id": 1, "product_symbol": "CALL", "side": "sell", "size": 2, "order_type": "market_order"}
        ),
    }
    journal = MemoryJournal("india:1")
    journal.strategy_intents = AsyncMock(return_value=[intent])
    journal.call = AsyncMock()
    lookup = AsyncMock(
        side_effect=AppError(404, "Not found") if lookup_fails else None,
        return_value={"result": {"id": 42, "unfilled_size": 0, "state": "closed"}},
    )
    client = SimpleNamespace(order_journal=journal, order_by_client_id=lookup, place_order=AsyncMock())
    database = SimpleNamespace(
        select=AsyncMock(return_value=[{"id": "execution"}]), upsert=AsyncMock(return_value=[{"id": "order"}])
    )
    row = {"id": "run", "risk_state": {"exclusiveFillAccounting": True, "exchangeAccount": "india:1"}}
    engine = TradingEngine(database, None)
    if lookup_fails:
        with pytest.raises(AppError):
            await engine.recover_order_records(row, client)
        database.upsert.assert_not_awaited()
    else:
        await engine.recover_order_records(row, client)
        saved = database.upsert.call_args.args[1]
        assert saved["client_order_id"] == "same-order"
        assert saved["delta_order_id"] == "42"
        assert saved["filled_size"] == "2"
        assert saved["execution_id"] == "execution"
    client.place_order.assert_not_awaited()
