import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.automation import build_account_context
from app.capital import CapitalPolicy
from app.default_strategies import default_strategy_definitions
from app.engine import TradingEngine, capital_budget
from app.errors import AppError
from automation_agent.charts import (
    render_candlestick_chart,
    render_open_interest_chart,
    render_order_book_chart,
    render_volatility_chart,
    render_volume_chart,
)
from automation_agent.market import compact_btc_market_packet, compact_delta_option_context
from automation_agent.storage import ChartArtifact, SupabaseChartStorage
from automation_agent.tools import materialize_live_definition
from news_agent.config import NewsAgentSettings


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
async def test_automatic_lots_use_half_of_total_usd_balance_by_default() -> None:
    definition = default_strategy_definitions(datetime(2026, 8, 25, tzinfo=UTC))[0]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())

    class Client:
        async def balances(self) -> dict:
            return {"result": [{"asset_symbol": "USD", "balance": "900", "available_balance": "900"}]}

    async def product_spec(_client, _symbol: str):
        return {"contract_value": 1, "initial_margin": 0}

    engine.product_spec = product_spec  # type: ignore[method-assign]
    resolved = [{"productSymbol": "C-BTC", "markPrice": "100", "position": "buy", "optionType": "call"}]

    sized = await engine.apply_automatic_lots(Client(), definition, resolved)  # type: ignore[arg-type]

    assert sized[0]["lots"] == 4


@pytest.mark.asyncio
async def test_automatic_lots_reject_one_lot_outside_selected_cap() -> None:
    definition = default_strategy_definitions(datetime(2026, 8, 25, tzinfo=UTC))[0]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())

    class Client:
        async def balances(self) -> dict:
            return {"result": [{"asset_symbol": "USD", "available_balance": "100"}]}

    async def product_spec(_client, _symbol: str):
        return {"contract_value": 1, "initial_margin": 0}

    engine.product_spec = product_spec  # type: ignore[method-assign]
    resolved = [{"productSymbol": "C-BTC", "markPrice": "100", "position": "buy", "optionType": "call"}]

    with pytest.raises(AppError) as caught:
        await engine.apply_automatic_lots(Client(), definition, resolved)  # type: ignore[arg-type]

    assert caught.value.code == "automatic_lot_too_large"


@pytest.mark.asyncio
async def test_half_balance_fits_one_long_straddle_lot_from_exchange_quotes() -> None:
    definition = default_strategy_definitions(datetime(2026, 8, 26, tzinfo=UTC))[2]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())

    class Client:
        async def balances(self) -> dict:
            return {
                "result": [
                    {"asset_symbol": "INR", "available_balance": "107.43"},
                    {"asset_symbol": "USD", "balance": "2.52", "available_balance": "2.52"},
                ]
            }

    async def product_spec(_client, _symbol: str):
        return {"contract_value": "0.001", "initial_margin": "0.5"}

    engine.product_spec = product_spec  # type: ignore[method-assign]
    resolved = [
        {
            "productSymbol": "C-BTC",
            "bestAsk": "289",
            "markPrice": "287",
            "spotPrice": "79000",
            "position": "buy",
            "optionType": "call",
        },
        {
            "productSymbol": "P-BTC",
            "bestAsk": "385",
            "markPrice": "383",
            "spotPrice": "79000",
            "position": "buy",
            "optionType": "put",
        },
    ]

    sized = await engine.apply_automatic_lots(Client(), definition, resolved)  # type: ignore[arg-type]

    assert {leg["lots"] for leg in sized} == {1}


@pytest.mark.asyncio
async def test_short_straddle_margin_estimate_matches_half_balance_example() -> None:
    definition = default_strategy_definitions(datetime(2026, 8, 26, tzinfo=UTC))[4]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())

    class Client:
        async def balances(self) -> dict:
            return {
                "result": [
                    {"asset_symbol": "INR", "available_balance": "107.43"},
                    {"asset_symbol": "USD", "balance": "2.52", "available_balance": "2.52"},
                ]
            }

    async def product_spec(_client, _symbol: str):
        return {"contract_value": "0.001", "initial_margin": "0.5"}

    engine.product_spec = product_spec  # type: ignore[method-assign]
    resolved = [
        {
            "productSymbol": "C-BTC",
            "bestBid": "500",
            "markPrice": "502",
            "spotPrice": "79000",
            "position": "sell",
            "optionType": "call",
        },
        {
            "productSymbol": "P-BTC",
            "bestBid": "362",
            "markPrice": "363",
            "spotPrice": "79000",
            "position": "sell",
            "optionType": "put",
        },
    ]

    sized = await engine.apply_automatic_lots(Client(), definition, resolved)  # type: ignore[arg-type]

    assert {leg["lots"] for leg in sized} == {1}


def test_capital_budget_supports_fraction_and_fixed_caps() -> None:
    available = Decimal("120")
    total = Decimal("200")

    assert capital_budget(available, total, "full_balance") == available
    assert capital_budget(available, total, "half_balance") == Decimal("100.0")
    assert capital_budget(available, total, "one_third_balance") == total / Decimal("3")
    assert capital_budget(available, total, "one_quarter_balance") == Decimal("50.00")
    assert capital_budget(available, total, "fixed_amount", 25) == Decimal("25")
    assert capital_budget(available, total, "fixed_amount", 250) == available
    assert capital_budget(Decimal("50"), Decimal("100"), "half_balance") == Decimal("50")


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


def test_agent_tools_return_summaries_instead_of_raw_candles_and_full_chain() -> None:
    candles = [{"open": 100, "high": 110, "low": 90, "close": 105, "baseVolume": 12} for _ in range(100)]
    market = {
        "source": "Binance Spot",
        "symbol": "BTCUSDT",
        "ticker": {"lastPrice": 105, "bestBid": 104, "bestAsk": 106},
        "analysis": {"atr": {"value": 10}, "sideways": {"probability": 70}},
        "orderBook": {"bids": [[104, 2]], "asks": [[106, 3]]},
        "recentTrades": [{"side": "buy", "quoteQuantity": 1000}],
        "realtime": {"connected": True},
        "deltaExecutionContext": {"openInterestUsd": 5_000_000},
        "timeframes": {"15 minute": {"candles": candles, "summary": {"returnPercent": 1.2}}},
    }
    options = [
        {
            "symbol": f"{kind}-BTC-{strike}-250826",
            "type": "call_options" if kind == "C" else "put_options",
            "expiry": "2026-08-25T12:00:00Z",
            "strike": strike,
            "spot": 100,
            "mark": 5,
            "openInterest": 10,
        }
        for strike in range(50, 151, 5)
        for kind in ("C", "P")
    ]

    compact_market = compact_btc_market_packet(market)
    compact_options = compact_delta_option_context(
        {"source": "Delta Exchange", "underlying": "BTC", "options": options}
    )

    assert "candles" not in json.dumps(compact_market)
    assert len(compact_options["nearbyOptions"]) == 10
    assert len(json.dumps(compact_options)) < len(json.dumps(options)) / 2


def test_private_chart_upload_returns_signed_url() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path.endswith("/storage/v1/bucket"):
            return httpx.Response(200, json={"name": "automation-charts"})
        if "/object/sign/" in request.url.path:
            return httpx.Response(200, json={"signedURL": "/object/sign/automation-charts/chart.png?token=x"})
        return httpx.Response(200, json={"Key": "chart.png"})

    settings = replace(
        NewsAgentSettings.load(),
        supabase_url="https://project.supabase.co",
        supabase_service_role_key="service-key",
    )
    storage = SupabaseChartStorage(settings, transport=httpx.MockTransport(handler))
    stored = storage.upload_run_charts(
        user_id="11111111-1111-4111-8111-111111111111",
        agent_run_id="22222222-2222-4222-8222-222222222222",
        charts=[ChartArtifact(id="btc-price", label="BTC price", alt_text="BTC price chart", content=b"png")],
    )

    assert stored[0].signed_url.startswith("https://project.supabase.co/storage/v1/object/sign/")
    assert any(path.endswith("/storage/v1/bucket") for _, path in requests)
    assert any("/storage/v1/object/automation-charts/" in path for _, path in requests)


@pytest.mark.asyncio
async def test_agent_account_context_excludes_balances() -> None:
    class Client:
        async def open_orders(self) -> dict:
            return {"result": [{"product_symbol": "BTCUSD", "side": "buy", "size": 1}]}

        async def positions(self) -> dict:
            return {"result": [{"product_symbol": "BTCUSD", "size": 1}]}

        async def balances(self) -> dict:
            raise AssertionError("balances must never be requested for model context")

        async def close(self) -> None:
            return None

    class Database:
        async def select(self, _table: str, _query: dict) -> list[dict]:
            return []

    class Engine:
        db = Database()

        async def capital_policy(self, _user_id: str) -> CapitalPolicy:
            return CapitalPolicy()

        async def client_for_user(self, _user_id: str) -> Client:
            return Client()

    context = await build_account_context(Engine(), "user-1")  # type: ignore[arg-type]

    assert "balances" not in context
    assert context["openOrders"][0]["product_symbol"] == "BTCUSD"
    assert context["positions"][0]["product_symbol"] == "BTCUSD"
