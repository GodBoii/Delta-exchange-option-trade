from datetime import UTC, datetime

from app.default_strategies import default_strategy_definitions
from scripts.shared_library import FIXED_IDS


def test_credit_spread_templates_are_bounded_market_order_strategies():
    definitions = {
        item.name: item.model_dump(mode="json", exclude_none=True)
        for item in default_strategy_definitions(datetime(2026, 9, 24, tzinfo=UTC))
        if item.name in FIXED_IDS
    }
    assert set(definitions) == set(FIXED_IDS)
    for name, definition in definitions.items():
        assert definition["name"] == name
        assert definition["enabledForAi"] is True
        assert definition["riskBasis"] == "defined_max_loss"
        assert [leg["position"] for leg in definition["legs"]] == ["buy", "sell"]
        assert all(leg["orderType"] == "market_order" for leg in definition["legs"])
