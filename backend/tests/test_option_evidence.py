import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.default_strategies import default_strategy_definitions
from app.exit_schedule import ExitChoice, resolve_exit_schedule, session_expiry, template_from_definition
from automation_agent.preview import build_preview, resolve_contracts
from automation_agent.team import calculator


def fixture(name="Long call"):
    now = datetime(2026, 10, 8, 0, 30, tzinfo=UTC)
    expiry = session_expiry(now)
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


def test_credit_spread_and_ratio_payoff_bounds():
    raw, _, _ = fixture()
    raw = {**raw, "contractValue": 1, "bestBid": 10, "bestAsk": 11}
    hedge = {**raw, "symbol": raw["symbol"].replace("-100-", "-110-"), "strike": 110, "bestBid": 4, "bestAsk": 5}
    legs = [
        {
            "productSymbol": raw["symbol"],
            "strike": 100,
            "optionType": "call",
            "position": "sell",
            "lots": 1,
            "orderType": "market_order",
        },
        {
            "productSymbol": hedge["symbol"],
            "strike": 110,
            "optionType": "call",
            "position": "buy",
            "lots": 1,
            "orderType": "market_order",
        },
    ]
    result = build_preview({}, legs, [raw, hedge], raw["observedAt"])
    assert result["netCreditUsd"] == 5
    assert result["expiryPayoff"] == {
        "breakevens": [105.0],
        "maximumLossUsd": 5.0,
        "maximumProfitUsd": 5.0,
        "basis": "gross, at expiry; software stops do not bound payoff",
    }
    legs[0].update(position="buy", lots=4)
    legs[1].update(position="sell", lots=2)
    result = build_preview({}, legs, [raw, hedge], raw["observedAt"])
    assert [row["contracts"] for row in result["legs"]] == [2, 1]
    assert result["netCreditUsd"] == -18
    assert result["expiryPayoff"]["breakevens"] == [109]
    assert result["expiryPayoff"]["maximumProfitUsd"] == "unbounded"


def test_verified_greek_scaling_and_limit_price_fill():
    raw, definition, _ = fixture()
    resolved = resolve_contracts(definition, [raw])
    days = (datetime.fromisoformat(raw["expiry"]).timestamp() * 1000 - raw["observedAt"]) / 86_400_000
    raw["vega"] = 0.001 * 100**2 * 0.4 * days / 365 / 100
    raw["theta"] = -0.001 * 100**2 * 0.4**2 / (2 * 365)
    result = build_preview(definition, resolved, [raw], raw["observedAt"])
    assert result["greeks"]["vega"] == pytest.approx(raw["vega"] * 0.001)
    assert result["localEstimates"]["scenarios"][2]["estimatedValueChangeUsd"] == pytest.approx(0.000250125)
    assert result["localEstimates"]["scenarios"][-1]["estimatedValueChangeUsd"] == pytest.approx(-9.13242e-8)
    resolved[0].update(orderType="limit_order", limitPrice="9")
    result = build_preview(definition, resolved, [raw], raw["observedAt"])
    assert result["legs"][0]["fill"]["estimatedPremium"] is None


def test_native_calculator_can_be_followed_by_cancellation():
    from automation_agent.tools import ActivationRecheckTools

    toolkit = calculator()
    assert set(toolkit.functions) == {"add", "subtract", "multiply", "divide", "exponentiate", "square_root"}
    assert json.loads(toolkit.multiply(2, 3))["result"] == 6
    assert "approve_strategy" in ActivationRecheckTools.__dict__
    assert "drop_strategy" in ActivationRecheckTools.__dict__


def test_calculation_then_drop_uses_only_the_assigned_proposal(monkeypatch):
    from uuid import uuid4

    from automation_agent.tools import ActivationRecheckTools

    calls = []

    class Data:
        def request_sync(self, path, args, **kwargs):
            calls.append((path, args))
            return {"outcome": "strategy_dropped", "success": True}

    monkeypatch.setattr("automation_agent.tools.runtime_data", lambda _: Data())
    proposal = str(uuid4())
    drop = ActivationRecheckTools(
        SimpleNamespace(), user_id="global", agent_run_id=str(uuid4()), proposal_id=proposal,
        evidence_is_valid=lambda: False,
    )
    assert json.loads(calculator().subtract(12, 10))["result"] == 2
    result = json.loads(drop.drop_strategy("Assigned strategy", datetime.now(UTC).isoformat(), "Thesis invalidated"))
    assert result["success"] is True
    assert calls[0][1]["proposalId"] == proposal
    assert calls[0][1]["drop"] is True


@pytest.mark.parametrize("trigger", ["manual", "new_york_session"])
def test_main_tools_offer_duration_only_for_evening_without_exit_calculator(monkeypatch, trigger):
    from uuid import uuid4

    from automation_agent.tools import AutomationStrategyTools

    monkeypatch.setattr("automation_agent.tools.runtime_data", lambda _: object())
    toolkit = AutomationStrategyTools(
        SimpleNamespace(trade_backend_internal_url="http://writer.test", analysis_service_secret="secret"),
        user_id="global", agent_run_id=str(uuid4()), market_snapshot_id=str(uuid4()),
        trigger=trigger,
    )
    expected = {"show_available_strategy", "select_strategy_and_time", "scheduled_next_agent_run"}
    assert set(toolkit.functions) == expected
    selection = toolkit.functions["select_strategy_and_time"]
    selection.process_entrypoint()
    properties = selection.parameters["properties"]
    assert "exit_choice" not in properties
    assert ("holding_hours" in properties) == (trigger == "new_york_session")
    if trigger == "new_york_session":
        assert properties["holding_hours"]["enum"] == [7, 11]
        assert "holding_hours" not in selection.parameters["required"]


def test_dry_run_constructs_inputs_without_provider_or_actions(monkeypatch):
    from scripts.agent_input_dry_run import catalogue, run_case

    raw, _, _ = fixture()
    case = {
        "asset": "BTC",
        "options": [raw],
        "catalogue": catalogue("BTC"),
        "market": {"source": "Binance Spot", "timeframes": {}},
    }
    for recheck in (False, True):
        result = run_case(case, "Same existing news summary", recheck=recheck, offline=True)
        assert result["simulatedActions"] == []
        assert result["toolCallLimit"] == (16 if recheck else 48)
        assert ("preview_selected_strategy" in result["registeredTools"]) is recheck
        assert "calculate_exit_time" not in result["registeredTools"]
        assert ("get_btc_market_packet" in result["registeredTools"]) is not recheck
        assert ("show_available_strategy" in result["registeredTools"]) is not recheck
        assert ("multiply" in result["registeredTools"]) is recheck
        assert ("drop_strategy" in result["registeredTools"]) is recheck
        assert ("approve_strategy" in result["registeredTools"]) is recheck


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
