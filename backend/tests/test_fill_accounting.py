from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_strategy import base_leg, base_strategy

from app.engine import AccountExposure, TradingEngine
from app.errors import AppError
from app.fill_accounting import exclusive_fill_positions
from app.strategy import strategy_level_metrics


def entry(product, order_id, price):
    return {
        "product_id": product,
        "delta_order_id": order_id,
        "side": "sell",
        "filled_size": "11",
        "contract_value": "0.001",
        "average_fill_price": price,
    }


def fill(identity, product, order_id, side, price, time, fee="0", size="11"):
    return {
        "id": identity,
        "product_id": product,
        "order_id": order_id,
        "side": side,
        "price": price,
        "size": size,
        "commission": fee,
        "created_at": f"2026-09-13T{time}Z",
    }


def incident():
    return [entry(1, "call-entry", "179"), entry(2, "put-entry", "101")], [
        fill("a", 1, "call-entry", "sell", "179", "00:15:01", "0.04"),
        fill("b", 2, "put-entry", "sell", "101", "00:15:02", "0.06"),
        fill("c", 2, "put-stop", "buy", "439", "08:39:39", "0.07556305"),
        fill("d", 1, "mobile-close", "buy", "0.5", "10:55:42", "0.04154685"),
    ]


def test_incident_cash_and_quantities_reconcile_with_duplicate_out_of_order_delivery():
    entries, fills = incident()
    positions = exclusive_fill_positions(entries, [*reversed(fills), fills[0]])
    assert all(item.remaining == 0 for item in positions.values())
    assert sum(item.realized_gross - item.fees for item in positions.values()) == Decimal("-1.97160990")


def test_closed_put_is_not_repriced_while_call_remains_open():
    entries, fills = incident()
    positions = exclusive_fill_positions(entries, fills[:3])
    assert positions[2].realized_gross == Decimal("-3.718")
    assert positions[2].unrealized(Decimal("9999")) == 0
    assert positions[1].unrealized(Decimal("0.5")) == Decimal("1.9635")


def test_partial_exit_keeps_only_remaining_quantity_at_risk():
    entries, fills = incident()
    fills = [fills[0], fill("partial", 1, "manual", "buy", "200", "01:00:00", size="3")]
    position = exclusive_fill_positions(entries[:1], fills)[1]
    assert position.remaining == -8
    assert position.realized_gross == Decimal("-0.063")
    assert position.unrealized(Decimal("100")) == Decimal("0.632")


def test_opening_fills_during_partial_exit_preserve_cash_and_remaining_cost():
    entries = [entry(1, "opening", "100")]
    entries[0]["filled_size"] = "4"
    fills = [
        fill("a", 1, "opening", "sell", "100", "00:00:00", size="2"),
        fill("b", 1, "closing", "buy", "120", "00:00:01", size="1"),
        fill("c", 1, "opening", "sell", "130", "00:00:02", size="2"),
    ]
    position = exclusive_fill_positions(entries, fills)[1]
    assert position.remaining == -3
    assert position.open_price == Decimal("120")
    assert position.realized_gross == Decimal("-0.020")
    assert position.realized_gross + position.unrealized(Decimal("140")) == Decimal("-0.080")


def test_cancelled_unfilled_entry_has_no_invented_position():
    entries = [entry(1, "opening", "100")]
    entries[0]["filled_size"] = "0"
    position = exclusive_fill_positions(entries, [])[1]
    assert position.remaining == 0
    assert position.realized_gross == 0


def test_other_strategy_opening_is_not_assigned_to_this_strategy():
    entries, fills = incident()
    fills.insert(2, fill("other", 1, "other-entry", "sell", "170", "00:16:00", size="1"))
    with pytest.raises(AppError) as error:
        exclusive_fill_positions(entries, fills)
    assert error.value.code == "fill_ownership_unknown"


@pytest.mark.parametrize("field,value", [("price", "NaN"), ("size", "1.5"), ("id", None)])
def test_incomplete_fill_facts_do_not_produce_guessed_accounting(field, value):
    entries, fills = incident()
    fills[0] = {**fills[0], field: value}
    with pytest.raises(AppError):
        exclusive_fill_positions(entries, fills)


def test_delayed_fee_does_not_block_risk_or_invent_final_net_pnl():
    entries, fills = incident()
    fills[0]["commission"] = None
    positions = exclusive_fill_positions(entries, fills[:3])
    assert positions[1].unrealized(Decimal("0.5")) == Decimal("1.9635")
    summary = TradingEngine.owned_settlement(positions)
    assert summary["accountingComplete"] is False
    assert summary["realizedPnl"] is None


def test_combined_policy_keeps_original_credit_after_put_exit():
    result = strategy_level_metrics(
        [
            {"side": "sell", "entry_price": "179", "mark_price": "0.5", "filled_size": 11, "contract_value": ".001"},
            {
                "side": "sell",
                "entry_price": "101",
                "mark_price": "0",
                "filled_size": 11,
                "contract_value": ".001",
                "remaining_size": 0,
                "realized_gross": "-3.718",
            },
        ],
        risk_basis="net_credit",
        stop_percent=Decimal("100"),
        take_profit_percent=Decimal("50"),
    )
    assert result["entry_value"] == Decimal("3.080")
    assert result["profit"] == Decimal("-1.7545")
    assert result["stop_triggered"] is False


async def test_complete_square_off_reacts_without_quoting_closed_put():
    entries, fills = incident()
    definition = base_strategy(
        riskMode="strategy_level",
        squareOff="complete",
        legs=[
            base_leg(id="call", position="sell", lots=11).model_dump(mode="json"),
            base_leg(id="put", optionType="put", position="sell", lots=11).model_dump(mode="json"),
        ],
    )
    for order, leg in zip(entries, definition.legs, strict=True):
        order.update({"leg_id": leg.id, "product_symbol": leg.id, "size": 11})
    row = {
        "id": "run",
        "user_id": "user",
        "definition_json": definition.model_dump(mode="json"),
        "risk_state": {"exclusiveFillAccounting": True},
    }
    database = SimpleNamespace(update=AsyncMock(return_value=[{"id": "run"}]))
    engine = TradingEngine(database, None)
    client = SimpleNamespace(ticker=AsyncMock(return_value={"result": {"mark_price": "0.5"}}), close=AsyncMock())
    engine.client_for_user = AsyncMock(return_value=client)
    engine.entry_orders = AsyncMock(return_value=entries)
    engine.reconcile_entry_fills = AsyncMock(return_value=entries)
    engine.owned_fill_positions = AsyncMock(return_value=exclusive_fill_positions(entries, fills[:3]))
    engine.contract_value = AsyncMock(return_value=Decimal(".001"))
    assert await engine.monitor_combined_strategy(row)
    client.ticker.assert_not_awaited()
    saved = database.update.call_args.args[1]
    assert saved["risk_state"]["exitReason"] == "external_leg_exit"
    assert saved["risk_state"]["exclusiveFillAccounting"] is True


async def test_flat_ordinary_fills_finalize_exact_result_and_release_claim():
    entries, fills = incident()
    row = {"id": "run", "user_id": "user", "risk_state": {"exclusiveFillAccounting": True}}
    database = SimpleNamespace(update=AsyncMock(return_value=[{"id": "run"}]))
    engine = TradingEngine(database, None)
    client = SimpleNamespace(order_journal=SimpleNamespace(call=AsyncMock()))
    engine.entry_orders = AsyncMock(return_value=entries)
    engine.account_exposure = AsyncMock(return_value=AccountExposure({}, ()))
    engine.live_position_size = AsyncMock(return_value=Decimal("0"))
    engine.owned_fill_positions = AsyncMock(return_value=exclusive_fill_positions(entries, fills))
    engine.release_capital_slot = AsyncMock()
    result = await engine.reconcile_run_if_flat(row, client)
    assert result["closureReason"] == "exchange_fills"
    assert Decimal(result["settlement"]["realizedPnl"]) == Decimal("-1.97160990")
    assert database.update.call_args.args[1]["status"] == "completed"
    client.order_journal.call.assert_awaited_once_with("orderIntents:releaseProducts", {"strategyId": "run"})


async def test_legacy_flat_run_recovers_ordinary_exits_without_convex_flag():
    entries, fills = incident()
    for order in entries:
        order["created_at"] = "2026-09-13T00:15:00Z"
    for item in fills:
        item["fill_type"] = "normal"
    row = {"id": "legacy", "user_id": "user", "risk_state": {}}
    database = SimpleNamespace(update=AsyncMock(return_value=[{"id": "legacy"}]))
    engine = TradingEngine(database, None)
    client = SimpleNamespace()
    engine.entry_orders = AsyncMock(return_value=entries)
    engine.account_exposure = AsyncMock(return_value=AccountExposure({}, ()))
    engine.live_position_size = AsyncMock(return_value=Decimal("0"))
    engine.enrich_contract_values = AsyncMock(return_value=entries)
    engine.fills_since = AsyncMock(return_value=fills)
    engine.release_capital_slot = AsyncMock()
    result = await engine.reconcile_run_if_flat(row, client)
    assert result["closureReason"] == "exchange_fills"
    assert Decimal(result["settlement"]["realizedPnl"]) == Decimal("-1.97160990")
    assert database.update.call_args.args[1]["status"] == "completed"
