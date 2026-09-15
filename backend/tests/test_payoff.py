from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from test_strategy import base_leg, base_strategy

from app.capital import CapitalPolicy
from app.engine import TradingEngine
from app.errors import AppError
from app.strategy import maximum_expiry_loss, strategy_level_metrics


def spread():
    return [
        {
            "side": "sell",
            "option_type": "call",
            "strike": "10000",
            "entry_price": "150",
            "mark_price": "500",
            "filled_size": 1,
            "contract_value": "1",
            "expiry": "2026-09-15",
        },
        {
            "side": "buy",
            "option_type": "call",
            "strike": "11000",
            "entry_price": "50",
            "mark_price": "50",
            "filled_size": 1,
            "contract_value": "1",
            "expiry": "2026-09-15",
        },
    ]


def test_defined_loss_uses_the_payoff_not_the_entry_credit():
    legs = spread()
    assert maximum_expiry_loss(legs) == 900
    metrics = strategy_level_metrics(
        legs, risk_basis="defined_max_loss", stop_percent=Decimal("50"), take_profit_percent=Decimal("50")
    )
    assert metrics["stop_value"] == 550
    assert metrics["stop_triggered"] is False
    legs[0]["mark_price"] = "610"
    assert (
        strategy_level_metrics(
            legs, risk_basis="defined_max_loss", stop_percent=Decimal("50"), take_profit_percent=Decimal("50")
        )["stop_triggered"]
        is True
    )


def test_a_ratio_with_uncovered_calls_is_not_defined_risk():
    legs = spread()
    legs[0]["filled_size"] = 2
    with pytest.raises(AppError, match="unbounded"):
        maximum_expiry_loss(legs)


def test_different_expiries_cannot_use_a_single_expiry_payoff():
    legs = spread()
    legs[1]["expiry"] = "2026-09-16"
    with pytest.raises(AppError, match="one known expiry"):
        maximum_expiry_loss(legs)


async def test_manual_unequal_lots_are_budgeted_individually():
    definition = base_strategy(
        lotsMode="manual",
        riskBasis="net_debit",
        legs=[
            base_leg(id="call", lots=5).model_dump(mode="json"),
            base_leg(id="put", optionType="put", lots=1).model_dump(mode="json"),
        ],
    )
    resolved = [{**leg.model_dump(mode="json"), "productSymbol": leg.id, "bestAsk": "100"} for leg in definition.legs]
    engine = TradingEngine(None, None)
    engine.product_spec = AsyncMock(return_value={"contract_value": ".001"})
    result = await engine.apply_automatic_lots(
        None, definition, resolved, CapitalPolicy(allocation_mode="full_balance"), (Decimal(".8"), Decimal(".8"))
    )
    assert [leg["lots"] for leg in result] == [5, 1]
