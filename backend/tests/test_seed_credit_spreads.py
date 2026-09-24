from datetime import UTC, datetime

from scripts.seed_credit_spreads import DEFAULT_IDS, new_defaults


def test_seed_payload_contains_only_new_bounded_market_order_strategies():
    definitions = new_defaults(datetime(2026, 9, 24, tzinfo=UTC))
    assert set(definitions) == set(DEFAULT_IDS)
    for name, definition in definitions.items():
        assert definition["name"] == name
        assert definition["enabledForAi"] is True
        assert definition["riskBasis"] == "defined_max_loss"
        assert [leg["position"] for leg in definition["legs"]] == ["buy", "sell"]
        assert all(leg["orderType"] == "market_order" for leg in definition["legs"])
