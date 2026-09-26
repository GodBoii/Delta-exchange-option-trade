import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_engine import settings
from test_strategy import base_leg, base_strategy

from app.delta import DeltaClient
from app.engine import TradingEngine, optional_decimal
from app.errors import AppError


@pytest.mark.parametrize("value", [None, "bad", "NaN", "Infinity", "-Infinity", "1.5"])
async def test_invalid_position_never_means_flat(value):
    client = SimpleNamespace(position=AsyncMock(return_value={"result": {"size": value}}))
    with pytest.raises(AppError, match="quantity") as error:
        await TradingEngine(None, settings()).live_position_size(client, 101)
    assert error.value.code == "position_size_unknown"


async def test_confirmed_zero_is_flat():
    client = SimpleNamespace(position=AsyncMock(return_value={"result": {"size": "0"}}))
    assert await TradingEngine(None, settings()).live_position_size(client, 101) == Decimal("0")


def test_optional_money_rejects_nonfinite_numbers():
    assert optional_decimal("NaN") is None
    assert optional_decimal("Infinity") is None
    assert optional_decimal("0") == Decimal("0")


@pytest.mark.parametrize("payload", [[], None, "unexpected"])
async def test_delta_rejects_invalid_response_envelopes(payload):
    client = DeltaClient(SimpleNamespace(delta_production_url="https://exchange.test"))
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(payload)))
    )
    try:
        with pytest.raises(AppError) as error:
            await client.ticker("BTCUSD")
        assert error.value.code == "invalid_delta_response"
    finally:
        await client.close()


async def test_order_lookup_uses_original_client_identity():
    client = DeltaClient(SimpleNamespace(delta_production_url="https://exchange.test"), "key", "secret")
    client.request = AsyncMock(return_value={"result": {"id": 42}})
    try:
        result = await client.order_by_client_id("known-order")
        assert result["result"]["id"] == 42
        client.request.assert_awaited_once_with("GET", "/v2/orders/client_order_id/known-order", authenticated=True)
    finally:
        await client.close()


async def test_duplicate_client_identity_error_is_not_a_definite_rejection():
    from app.errors import DeltaOrderRejected

    client = DeltaClient(SimpleNamespace(delta_production_url="https://exchange.test"), "key", "secret")
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "success": False,
                    "error": {"code": "duplicate_client_order_id"},
                },
            )
        )
    )
    try:
        with pytest.raises(AppError) as error:
            await client.place_order({"client_order_id": "existing"})
        assert not isinstance(error.value, DeltaOrderRejected)
    finally:
        await client.close()


def entry_engine(*, mark="100", emergency=True, failure=None):
    definition = base_strategy(
        riskMode="strategy_level",
        emergencyExitEnabled=emergency,
        emergencyStopLossPercent=300,
        legs=[base_leg(position="sell").model_dump(mode="json")],
    )
    row = {"id": "strategy-1234", "user_id": "user", "definition_json": definition.model_dump(mode="json")}
    resolved = [
        {
            **definition.legs[0].model_dump(mode="json"),
            "productId": 101,
            "productSymbol": "CALL",
            "markPrice": mark,
            "contractValue": "0.001",
        }
    ]
    client = SimpleNamespace(
        place_order=AsyncMock(
            side_effect=failure,
            return_value={
                "result": {"id": 123, "state": "closed", "unfilled_size": 0, "average_fill_price": "100"},
            },
        ),
        close=AsyncMock(),
    )
    db = SimpleNamespace(insert=AsyncMock(return_value=[{"id": "execution"}]), update=AsyncMock(return_value=[]))
    engine = TradingEngine(db, settings())
    engine.startup_recovered = True
    engine.strategy_by_id = AsyncMock(return_value=row)
    engine.client_for_user = AsyncMock(return_value=client)
    engine.capital_policy = AsyncMock(
        return_value=SimpleNamespace(
            allocation_mode="half_balance",
            capital_amount=None,
            as_json=lambda: {},
        )
    )
    engine.usd_capital = AsyncMock(return_value=(Decimal("100"), Decimal("100")))
    engine.reserve_capital_slot = AsyncMock(return_value={"slot": 1, "created": True})
    engine.release_capital_slot = AsyncMock()
    engine.resolve_strategy = AsyncMock(return_value=resolved)
    engine.apply_automatic_lots = AsyncMock(return_value=resolved)
    engine.claim_strategy = AsyncMock()
    return engine, client


@pytest.mark.parametrize("mark", [None, "0", "NaN", "Infinity"])
async def test_invalid_mark_blocks_entry_before_exchange_submission(mark):
    engine, client = entry_engine(mark=mark)
    with pytest.raises(AppError, match="mark"):
        await engine.execute_entry("strategy-1234")
    client.place_order.assert_not_awaited()
    engine.release_capital_slot.assert_awaited_once()


@pytest.mark.parametrize("enabled", [False, True])
async def test_emergency_toggle_controls_the_actual_order(enabled):
    engine, client = entry_engine(emergency=enabled)
    await engine.execute_entry("strategy-1234")
    payload = client.place_order.call_args.args[0]
    assert ("bracket_stop_loss_price" in payload) == enabled


async def test_lost_order_response_retains_reserved_capital():
    engine, _ = entry_engine(failure=AppError(502, "Response lost", "delta_unreachable"))
    with pytest.raises(AppError):
        await engine.execute_entry("strategy-1234")
    engine.release_capital_slot.assert_not_awaited()
    recorded = [call.args[1] for call in engine.db.insert.call_args_list if call.args[0] == "execution_orders"]
    assert recorded[0]["state"] == "unknown"


async def test_missing_ai_recheck_is_pending_but_manual_strategy_is_ready():
    db = SimpleNamespace(
        select=AsyncMock(
            side_effect=[
                [{"id": "proposal", "strategy_id": "ai-strategy"}],
                [],
            ]
        )
    )
    states = await TradingEngine(db, settings()).activation_recheck_states(["ai-strategy", "manual-strategy"])
    assert states == {"ai-strategy": "pending", "manual-strategy": "ready"}


async def test_strategy_scan_reaches_later_pages():
    pages = [[{"id": f"{index:04d}"} for index in range(100)], [{"id": "0100"}]]
    db = SimpleNamespace(select=AsyncMock(side_effect=pages))
    rows = await TradingEngine(db, settings()).strategy_pages({"status": "eq.active", "select": "id"})
    assert len(rows) == 101
    assert db.select.call_args.args[1]["id"] == "gt.0099"


async def test_strategy_scan_rejects_a_nonadvancing_backend():
    page = [{"id": f"{index:04d}"} for index in range(100)]
    db = SimpleNamespace(select=AsyncMock(return_value=page))
    with pytest.raises(AppError, match="pagination"):
        await TradingEngine(db, settings()).strategy_pages({"status": "eq.active", "select": "id"})


async def test_failed_account_close_keeps_protective_orders():
    from app.engine import AccountExposure
    from app.errors import DeltaOrderRejected

    engine = TradingEngine(None, settings())
    engine.startup_recovered = True
    client = SimpleNamespace(cancel_order=AsyncMock(), close=AsyncMock())
    engine.client_for_user = AsyncMock(return_value=client)
    engine.account_exposure = AsyncMock(
        return_value=AccountExposure(
            {},
            (
                {"id": 10, "product_id": 1, "stop_order_type": "stop_loss_order"},
                {"id": 11, "product_id": 1, "stop_order_type": None, "reduce_only": False},
            ),
        )
    )
    engine.live_position_size = AsyncMock(return_value=Decimal("-2"))
    engine.submit_verified_close = AsyncMock(side_effect=DeltaOrderRejected(400, "No liquidity"))
    with pytest.raises(DeltaOrderRejected):
        await engine.close_account_position("user", 1)
    client.cancel_order.assert_awaited_once_with(11, 1)
