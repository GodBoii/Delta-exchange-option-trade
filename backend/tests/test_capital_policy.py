"""Asset budgets share one wallet while preserving legacy account policies."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.analysis_admission import evaluate
from app.capital import CapitalPolicy, fits_capital_limits, policy_budget, policy_from_row
from app.engine import TradingEngine
from app.models import CapitalSettingsUpdate
from tests.test_strategy import base_strategy


def asset_policy(**extra):
    return policy_from_row({
        "allocation_mode": "one_quarter_balance",
        "asset_overrides": {
            "BTC": {"allocationMode": "custom_percentage", "allocationPercent": 20},
            "ETH": {"allocationMode": "custom_percentage", "allocationPercent": 10},
        }, **extra,
    })


def test_independent_asset_budgets_and_inherited_default():
    policy = asset_policy()
    assert policy_budget(Decimal(10000), Decimal(10000), policy, "BTC") == 2000
    assert policy_budget(Decimal(10000), Decimal(10000), policy, "ETH") == 1000
    assert policy.concurrency_limit(Decimal(10000)) == 100
    assert policy_budget(Decimal(10000), Decimal(10000), policy) == 2500
    inherited = policy_from_row({"allocation_mode": "one_quarter_balance", "asset_overrides": {
        "ETH": {"allocationMode": "fixed_amount", "capitalAmount": 100},
    }})
    assert policy_budget(Decimal(10000), Decimal(10000), inherited, "BTC") == 2500
    assert policy_budget(Decimal(50), Decimal(10000), inherited, "ETH") == 50


@pytest.mark.parametrize("parts", [1, 5, 10, 20, 100])
def test_equal_parts_use_total_balance(parts):
    policy = policy_from_row({"allocation_mode": "equal_parts", "division_count": parts})
    assert policy_budget(Decimal(10000), Decimal(10000), policy) == Decimal(10000) / parts
    assert policy.concurrency_limit(Decimal(10000)) == parts


def test_overall_and_asset_caps_account_for_existing_reservations():
    policy = asset_policy(overall_limit={"mode": "percentage", "value": 60},
                          asset_limits={"ETH": {"mode": "fixed_amount", "value": 2000}})
    assert fits_capital_limits(policy, Decimal(10000), "ETH", Decimal(1000), Decimal(5000), {"ETH": Decimal(1000)})
    assert not fits_capital_limits(policy, Decimal(10000), "ETH", Decimal(1000), Decimal(5500), {})
    assert not fits_capital_limits(policy, Decimal(10000), "ETH", Decimal(1000), Decimal(3000), {"ETH": Decimal(2000)})
    # BTC can use free capital even when ETH's own cap is full.
    assert fits_capital_limits(policy, Decimal(10000), "BTC", Decimal(2000), Decimal(3000), {"ETH": Decimal(2000)})


def test_mixed_scheduled_commitments_use_their_own_asset_budget():
    policy = asset_policy(overall_limit={"mode": "percentage", "value": 30})
    row = {"allocation_mode": "one_quarter_balance", "asset_overrides": policy.as_json()["assetOverrides"],
           "overall_limit": policy.overall_limit}
    pending = {"owner": "account", "capital": row, "asset": "BTC",
               "start": "2026-10-12T00:00:00+00:00", "end": "2026-10-12T01:00:00+00:00"}
    snapshot = {"eligible": True, "owner": "account", "capital": row, "asset": "ETH",
                "occupied": 0, "reserved": "0", "commitments": [pending]}
    assert evaluate(snapshot, Decimal(10000), Decimal(10000)) == "ready"
    snapshot["asset"] = "BTC"
    assert evaluate(snapshot, Decimal(10000), Decimal(10000)) == "pending"
    snapshot.update(asset="ETH", occupied=1, reserved="3000", reservedByAsset={"BTC": "3000"})
    assert evaluate(snapshot, Decimal(10000), Decimal(10000)) == "full"


@pytest.mark.parametrize("body", [
    {"allocationMode": "equal_parts"},
    {"allocationMode": "equal_parts", "divisionCount": 101},
    {"allocationMode": "equal_parts", "divisionCount": 2.5},
    {"allocationMode": "custom_percentage", "allocationPercent": 0},
    {"allocationMode": "custom_percentage", "allocationPercent": 101},
    {"allocationMode": "custom_percentage", "allocationPercent": float("nan")},
    {"allocationMode": "fixed_amount", "capitalAmount": float("inf")},
    {"assetOverrides": {"SOL": {"allocationMode": "full_balance"}}},
    {"assetOverrides": {"ETH": {"allocationMode": "fixed_amount"}}},
    {"overallLimit": {"mode": "percentage", "value": 101}},
    {"assetLimits": {"BTC": {"mode": "fixed_amount", "value": -1}}},
    {"maximumConcurrentStrategies": 0},
])
def test_invalid_capital_settings_are_rejected_at_the_api_boundary(body):
    with pytest.raises(ValidationError):
        CapitalSettingsUpdate.model_validate(body)


def test_legacy_updates_preserve_extensions_and_explicit_resets_clear_them():
    assert CapitalSettingsUpdate(allocationMode="half_balance").extensions() == {}
    reset = CapitalSettingsUpdate(assetOverrides={}, overallLimit=None, maximumConcurrentStrategies=None)
    assert reset.extensions() == {"asset_overrides": {}, "overall_limit": None, "maximum_concurrent_strategies": None}
    assert CapitalPolicy().as_json() == {"allocationMode": "half_balance", "capitalAmount": None}


def test_explicit_concurrency_limit_is_independent_from_budget():
    policy = asset_policy(maximum_concurrent_strategies=3)
    assert policy.concurrency_limit(Decimal(10000)) == 3
    assert policy_budget(Decimal(10000), Decimal(10000), policy, "ETH") == 1000


@pytest.mark.parametrize("asset,expected", [("BTC", 196), ("ETH", 98)])
async def test_actual_lot_sizing_uses_the_strategy_asset_override(asset, expected):
    definition = base_strategy(lotsMode="auto", riskBasis="net_debit", instrument={
        "index": f"{asset}USD", "underlying": asset, "underlyingFrom": "cash",
    })
    resolved = [{**leg.model_dump(mode="json"), "productSymbol": leg.id, "bestAsk": "100"}
                for leg in definition.legs]
    engine = TradingEngine(None, None)
    engine.product_spec = AsyncMock(return_value={"contract_value": ".001"})
    try:
        result = await engine.apply_automatic_lots(None, definition, resolved, asset_policy(),
                                                  (Decimal(100), Decimal(100)))
        assert [leg["lots"] for leg in result] == [expected]
    finally:
        await engine.close()
