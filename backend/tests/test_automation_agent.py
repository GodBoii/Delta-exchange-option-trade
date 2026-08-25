from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.default_strategies import default_strategy_definitions
from app.engine import TradingEngine
from app.errors import AppError
from automation_agent.charts import (
    render_candlestick_chart,
    render_open_interest_chart,
    render_order_book_chart,
    render_volatility_chart,
    render_volume_chart,
)
from automation_agent.tools import materialize_live_definition


def option_context(*expiries: datetime) -> dict:
    return {"options": [{"expiry": expiry.isoformat()} for expiry in expiries]}


def test_materializes_same_day_hold_to_expiry_schedule() -> None:
    activation = datetime(2026, 8, 25, 6, tzinfo=UTC)
    definition = default_strategy_definitions(activation)[4].model_dump(mode="json", exclude_none=True)
    expiry = datetime(2026, 8, 25, 12, tzinfo=UTC)

    live, exit_at = materialize_live_definition(
        definition,
        activation=activation,
        option_context=option_context(expiry, expiry + timedelta(days=1)),
    )

    assert datetime.fromisoformat(live["entry"]["entryAt"].replace("Z", "+00:00")) == activation
    assert exit_at == expiry - timedelta(minutes=5)
    assert {leg["expiry"] for leg in live["legs"]} == {expiry.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()}
    assert "selectionCriteria" not in live


def test_resolves_seven_day_policy_to_first_later_listed_expiry() -> None:
    activation = datetime(2026, 8, 25, 6, tzinfo=UTC)
    definition = default_strategy_definitions(activation)[0].model_dump(mode="json", exclude_none=True)
    expected = datetime(2026, 9, 2, 18, 30, tzinfo=UTC)

    live, _ = materialize_live_definition(
        definition,
        activation=activation,
        option_context=option_context(
            datetime(2026, 8, 30, 18, 30, tzinfo=UTC),
            expected,
            datetime(2026, 9, 9, 18, 30, tzinfo=UTC),
        ),
    )

    assert {leg["expiry"] for leg in live["legs"]} == {expected.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()}


@pytest.mark.asyncio
async def test_automatic_lots_use_one_third_balance_and_hard_cap() -> None:
    definition = default_strategy_definitions(datetime(2026, 8, 25, tzinfo=UTC))[0]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())

    class Client:
        async def balances(self) -> dict:
            return {"result": [{"available_balance": "900"}]}

    async def contract_value(_client, _symbol: str):
        return 1

    engine.contract_value = contract_value  # type: ignore[method-assign]
    resolved = [{"productSymbol": "C-BTC", "markPrice": "100", "position": "buy", "optionType": "call"}]

    sized = await engine.apply_automatic_lots(Client(), definition, resolved)  # type: ignore[arg-type]

    assert sized[0]["lots"] == 1


@pytest.mark.asyncio
async def test_automatic_lots_reject_one_lot_outside_slot() -> None:
    definition = default_strategy_definitions(datetime(2026, 8, 25, tzinfo=UTC))[0]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())

    class Client:
        async def balances(self) -> dict:
            return {"result": [{"available_balance": "200"}]}

    async def contract_value(_client, _symbol: str):
        return 1

    engine.contract_value = contract_value  # type: ignore[method-assign]
    resolved = [{"productSymbol": "C-BTC", "markPrice": "100", "position": "buy", "optionType": "call"}]

    with pytest.raises(AppError) as caught:
        await engine.apply_automatic_lots(Client(), definition, resolved)  # type: ignore[arg-type]

    assert caught.value.code == "automatic_lot_too_large"


def test_all_agent_chart_types_render_non_empty_pngs() -> None:
    candles = [
        {
            "open": 80_000 + index,
            "high": 80_100 + index,
            "low": 79_900 + index,
            "close": 80_020 + index * 2,
            "volume": 10 + index,
        }
        for index in range(40)
    ]
    charts = [
        render_candlestick_chart("15 minute", candles),
        render_volume_chart("15 minute", candles),
        render_volatility_chart("15 minute", candles, 365 * 24 * 4),
        render_order_book_chart(
            {
                "bids": [[79_999 - index, 1 + index] for index in range(10)],
                "asks": [[80_001 + index, 1 + index] for index in range(10)],
            }
        ),
        render_open_interest_chart([{"close": 1_000 + index * 5} for index in range(40)]),
    ]

    assert all(chart.startswith(b"\x89PNG") and len(chart) > 1_000 for chart in charts)
