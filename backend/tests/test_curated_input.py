import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.default_strategies import default_strategy_definitions
from app.exit_schedule import ExitChoice, resolve_exit_schedule, template_from_definition
from automation_agent.curated import chart_notes, market_input
from automation_agent.preview import build_preview, resolve_contracts
from automation_agent.team import calculator


def fixture(name="Long call"):
    now = datetime.now(UTC)
    expiry = now + timedelta(days=2)
    template = next(s for s in default_strategy_definitions(now) if s.name == name)
    raw = {
        "symbol": f"C-BTC-100-{expiry:%d%m%y}",
        "expiry": expiry.isoformat(),
        "type": "call_options",
        "productId": 1,
        "strike": 100,
        "spot": 100,
        "mark": 9,
        "bestBid": 8,
        "bestAsk": 10,
        "contractValue": 0.001,
        "observedAt": int(now.timestamp() * 1000),
        "impliedVolatility": 0.4,
        "delta": 0.5,
        "gamma": 0.001,
        "vega": None,
        "theta": None,
        "depth": {"sell": [{"price": 10, "size": 2}], "buy": [{"price": 8, "size": 2}]},
    }
    definition, schedule = resolve_exit_schedule(
        template_from_definition(template.model_dump(mode="json")),
        entry_at=now + timedelta(minutes=10),
        choice=ExitChoice(kind="specific_time", exit_at=now + timedelta(hours=1)),
        options=[raw],
    )
    return raw, definition, schedule


def test_curated_input_owns_numbers_and_has_no_market_arrays():
    packet = {
        "symbol": "ETHUSDT",
        "ticker": {"lastPrice": 100},
        "analysis": {
            "sidewaysProbability": 70,
            "vwap": 99,
            "historicalVolatility": {"annualizedPercent": 40},
            "orderBook": {"bidDepth": 99},
        },
        "orderBook": {"bids": [[99, 2]], "asks": [[101, 3]]},
        "recentTrades": [{"time": 100, "side": "buy", "quoteQuantity": 12}],
        "timeframes": {"1 minute": {"candles": [{"close": 123}], "summary": {"returnPercent": 1}}},
    }
    result = market_input(packet, "ETH")
    text = json.dumps(result)
    assert all(key not in text for key in ('"candles"', '"bids"', '"asks"', '"recentTrades"', '"Top20'))
    assert result["liquidity"]["current"]["bidLevels"] == 1
    assert result["flow"]["recentSample"]["start"] == 100
    assert result["history"]["realizedVolatilityAnnualizedPercent"] == 40
    assert "orderBook" not in result
    assert chart_notes({"a": {"values": {"price": 100}, "readingNotes": ["read"]}}) == {"a": {"readingNotes": ["read"]}}


def test_exact_call_payoff_units_greeks_and_advisory_fill():
    raw, definition, _ = fixture()
    resolved = resolve_contracts(definition, [raw])
    result = build_preview(definition, resolved, [raw], raw["observedAt"])
    assert result["netCreditUsd"] == -0.01
    assert result["expiryPayoff"]["breakevens"] == [110]
    assert result["expiryPayoff"]["maximumLossUsd"] == 0.01
    assert result["expiryPayoff"]["maximumProfitUsd"] == "unbounded"
    assert result["greeks"]["delta"] == 0.0005
    assert result["greeks"]["vega"] is None
    assert result["legs"][0]["fill"]["estimatedPremium"] == 10
    assert result["localEstimates"]["scenarios"][-1]["estimatedValueChangeUsd"] is None


def test_short_call_unbounded_loss_and_unavailable_depth_is_advisory():
    raw, definition, _ = fixture()
    resolved = resolve_contracts(definition, [raw])
    resolved[0]["position"] = "sell"
    raw["depth"] = {}
    result = build_preview(definition, resolved, [raw], raw["observedAt"])
    assert result["expiryPayoff"]["maximumLossUsd"] == "unbounded"
    assert result["expiryPayoff"]["maximumProfitUsd"] == 0.008
    assert result["legs"][0]["fill"]["estimatedPremium"] is None
    with pytest.raises(ValueError, match="Fresh"):
        build_preview(definition, resolved, [raw], raw["observedAt"] + 31_000)


def test_native_calculator_can_be_followed_by_cancellation():
    from automation_agent.tools import DropStrategyTools

    toolkit = calculator()
    assert set(toolkit.functions) == {"add", "subtract", "multiply", "divide", "exponentiate", "square_root"}
    assert json.loads(toolkit.multiply(2, 3))["result"] == 6
    assert "drop_strategy" in DropStrategyTools.__dict__


def test_calculation_then_drop_uses_only_the_assigned_proposal(monkeypatch):
    from uuid import uuid4

    from automation_agent.tools import DropStrategyTools

    calls = []

    class Data:
        def request_sync(self, path, args, **kwargs):
            calls.append((path, args))
            return {"outcome": "strategy_dropped", "success": True}

    monkeypatch.setattr("automation_agent.tools.runtime_data", lambda _: Data())
    proposal = str(uuid4())
    drop = DropStrategyTools(SimpleNamespace(), user_id="global", agent_run_id=str(uuid4()), proposal_id=proposal)
    assert json.loads(calculator().subtract(12, 10))["result"] == 2
    result = json.loads(drop.drop_strategy("Assigned strategy", datetime.now(UTC).isoformat(), "Thesis invalidated"))
    assert result["success"] is True
    assert calls[0][1]["proposalId"] == proposal
    assert calls[0][1]["drop"] is True


def test_curated_tools_require_matching_preview_without_scheduling(monkeypatch):
    from uuid import uuid4

    from automation_agent.tools import AutomationStrategyTools

    monkeypatch.setattr("automation_agent.tools.runtime_data", lambda _: object())
    toolkit = AutomationStrategyTools(
        SimpleNamespace(trade_backend_internal_url="http://writer.test", analysis_service_secret="secret"),
        user_id="global",
        agent_run_id=str(uuid4()),
        market_snapshot_id=str(uuid4()),
        curated=True,
    )
    assert set(toolkit.functions) == {"preview_strategy", "select_strategy_and_time", "scheduled_next_agent_run"}
    toolkit.functions["preview_strategy"].process_entrypoint()
    schema = toolkit.functions["preview_strategy"].parameters["properties"]["exit_choice"]
    assert "kind" in str(schema)
    activation = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    with pytest.raises(ValueError, match="preview_strategy"):
        toolkit._select_strategy_and_time(
            saved_id=str(uuid4()),
            saved_strategy_version=1,
            activation_time=activation,
            ai_confidence=0.5,
            reasoning_summary="test",
            supporting_signals=[],
            invalidation_signals=[],
            exit_choice={"kind": "specific_time", "exit_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        )


def test_paired_dry_run_constructs_inputs_without_provider_or_actions(monkeypatch):
    from scripts.agent_input_dry_run import catalogue, run_case

    raw, _, _ = fixture()
    case = {
        "asset": "BTC",
        "options": [raw],
        "catalogue": catalogue("BTC"),
        "market": {"source": "Binance Spot", "timeframes": {}},
    }
    for curated in (False, True):
        for recheck in (False, True):
            result = run_case(case, "Same existing news summary", curated=curated, recheck=recheck, offline=True)
            assert result["simulatedActions"] == []
            if curated:
                assert "get_btc_market_packet" not in result["registeredTools"]
                assert "show_available_strategy" not in result["registeredTools"]
                assert "multiply" in result["registeredTools"]
                assert ("drop_strategy" in result["registeredTools"]) is recheck


@pytest.mark.asyncio
async def test_watchlist_contains_only_expiries_not_accounts():
    from app.market_watch import publish_watchlists

    expiry = (datetime.now(UTC) + timedelta(days=40)).replace(hour=12).isoformat()
    date = datetime.fromisoformat(expiry).date().isoformat()
    calls = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"underlying": "BTC", "options": [{"expiry": expiry}]})
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["X-Analysis-Secret"] == "secret"
        return httpx.Response(200, json={"success": True})

    async def select(*args):
        return [
            {
                "definition_json": {"instrument": {"underlying": "BTC"}, "legs": [{"expiry": date}]},
                "user_id": "private-account",
            }
        ]

    db = SimpleNamespace(settings=SimpleNamespace(analysis_service_secret="secret"), select=select)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await publish_watchlists(db, client)
    assert calls == [{"expiries": [expiry]}]
