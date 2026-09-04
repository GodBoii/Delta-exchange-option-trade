from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.capital import CapitalPolicy
from app.default_strategies import default_strategy_definitions
from app.engine import TradingEngine
from app.strategy import resolve_leg
from automation_agent.tools import materialize_live_definition

NOW = datetime(2026, 9, 4, 14, tzinfo=UTC)
DEFINITIONS = default_strategy_definitions(NOW)
ADDED = DEFINITIONS[6:]
CHAIN = [
    {
        "product_id": strike + offset,
        "symbol": f"{kind}-BTC-{strike}-050926",
        "contract_type": contract_type,
        "strike_price": str(strike),
        "spot_price": "80000",
        "mark_price": "100",
        "quotes": {"best_bid": "99", "best_ask": "101"},
    }
    for strike in range(78000, 82200, 200)
    for offset, kind, contract_type in ((0, "C", "call_options"), (1, "P", "put_options"))
]


@pytest.mark.parametrize("definition", ADDED, ids=lambda definition: definition.name)
def test_new_templates_have_no_hedge_legs_and_resolve_after_daily_expiry(definition):
    assert len({leg.position for leg in definition.legs}) == 1
    assert definition.riskBasis != "defined_max_loss"
    assert all("protective" not in (leg.role or "") for leg in definition.legs)
    assert definition.squareOff == "complete"
    assert definition.holdingMode == "intraday"
    expiry = NOW.replace(hour=12) + timedelta(days=1 if definition.expiryPolicy == "next_day" else 7)
    live, exit_at = materialize_live_definition(
        definition.model_dump(mode="json"),
        activation=NOW,
        option_context={"options": [{"expiry": expiry.isoformat()}]},
    )
    assert {leg["expiry"] for leg in live["legs"]} == {expiry.date().isoformat()}
    assert exit_at == NOW + timedelta(hours=7)
    for leg in definition.legs:
        resolved = resolve_leg(leg, CHAIN)
        signed_distance = resolved["strike"] - 80000
        if leg.strikeMode != "atm":
            expected_sign = 1 if (leg.optionType == "call") == (leg.strikeMode == "otm") else -1
            assert signed_distance == expected_sign * 400


class Database:
    def __init__(self, definition):
        self.row = {
            "id": "strategy-1",
            "user_id": "user-1",
            "status": "scheduled",
            "definition_json": definition.model_dump(mode="json"),
            "entry_execution_at": None,
        }
        self.orders = []

    async def select(self, table, _query):
        assert table == "strategies"
        return [deepcopy(self.row)]

    async def insert(self, table, payload):
        if table == "execution_orders":
            self.orders.append(deepcopy(payload))
        return [{"id": "execution-1", **payload}]

    async def update(self, table, payload, _query):
        if table == "strategies":
            self.row.update(payload)
        return [{"id": "strategy-1"}]

    async def rpc(self, function, _payload):
        assert function == "reserve_strategy_capital_slot"
        return {"slot": 1, "created": True, "occupiedBefore": 0}


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda definition: definition.name)
async def test_builtin_entry_preserves_sizing_and_short_emergency_stops(definition):
    database = Database(definition)
    client = SimpleNamespace(
        balances=AsyncMock(
            return_value={"result": [{"asset_symbol": "USD", "balance": "100", "available_balance": "100"}]}
        ),
        option_chain=AsyncMock(return_value={"result": CHAIN}),
        product=AsyncMock(return_value={"result": {"contract_value": "0.001", "initial_margin": "0.5"}}),
        place_order=AsyncMock(
            return_value={"result": {"id": 123, "unfilled_size": 0, "average_fill_price": "100", "state": "closed"}}
        ),
        close=AsyncMock(),
    )
    engine = TradingEngine(database, SimpleNamespace())
    engine.client_for_user = AsyncMock(return_value=client)
    engine.capital_policy = AsyncMock(return_value=CapitalPolicy())

    await engine.execute_entry("strategy-1")

    assert database.row["status"] == "active"
    assert len(database.orders) == len(definition.legs)
    for call in client.place_order.call_args_list:
        order = call.args[0]
        assert order["size"] >= 1
        assert order["reduce_only"] is False
        if order["side"] == "sell":
            assert Decimal(order["bracket_stop_loss_price"]) == Decimal("400")
            assert order["bracket_stop_trigger_method"] == "mark_price"
        else:
            assert "bracket_stop_loss_price" not in order


@pytest.mark.parametrize("definition", ADDED, ids=lambda definition: definition.name)
@pytest.mark.parametrize("exit_reason", ["stop_loss", "take_profit"])
async def test_new_templates_trigger_the_existing_monitor(definition, exit_reason):
    database = Database(definition)
    database.row["status"] = "active"
    client = SimpleNamespace(ticker=AsyncMock(), close=AsyncMock())
    engine = TradingEngine(database, SimpleNamespace())
    engine.client_for_user = AsyncMock(return_value=client)
    engine.contract_value = AsyncMock(return_value=Decimal("0.001"))
    orders = [
        {
            "leg_id": leg.id,
            "product_symbol": f"product-{leg.id}",
            "side": leg.position,
            "size": 1,
            "filled_size": 1,
            "average_fill_price": "100",
        }
        for leg in definition.legs
    ]
    engine.entry_orders = AsyncMock(return_value=orders)
    engine.reconcile_entry_fills = AsyncMock(return_value=orders)
    # A debit stop of 100% is full premium loss. Zero marks are intentionally rejected by the engine.
    if definition.riskBasis == "net_debit":
        copy = definition.model_copy(update={"stopLossPercent": 50})
        database.row["definition_json"] = copy.model_dump(mode="json")
        mark = "50" if exit_reason == "stop_loss" else "150"
    else:
        mark = "200" if exit_reason == "stop_loss" else "50"
    client.ticker.return_value = {"result": {"mark_price": mark}}

    assert await engine.monitor_combined_strategy(database.row)
    assert database.row["risk_state"]["exitReason"] == exit_reason
    assert database.row["status"] == "executing_exit"
