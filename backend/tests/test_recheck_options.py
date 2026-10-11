"""Stage boundaries, assigned-contract refreshes and approval failure cases."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from agno.run.agent import RunOutput
from test_curated_input import fixture

from app import automation_schedule
from automation_agent import recheck, team, tools
from automation_agent.assets import PROFILES
from automation_agent.curated import assigned_option_overview
from automation_agent.recheck import RecheckOptionTools


def assigned(asset="BTC"):
    raw, definition, schedule = fixture()
    if asset == "ETH":
        raw["symbol"] = raw["symbol"].replace("BTC", "ETH")
        definition["name"] = "ETH Long call"
        definition["instrument"].update(index="ETHUSD", underlying="ETH")
    return raw, {
        "name": definition["name"], "definition": definition,
        "activationTime": schedule["entryUtc"], "exitTime": schedule["exitUtc"],
    }


def freeze(monkeypatch, timestamp):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(timestamp / 1000, tz or UTC)

    monkeypatch.setattr(recheck, "datetime", FrozenDatetime)
    monkeypatch.setattr(tools, "datetime", FrozenDatetime)
    return FrozenDatetime


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_recheck_refresh_is_bound_read_only_and_uses_actual_hold(monkeypatch, asset):
    raw, strategy = assigned(asset)
    freeze(monkeypatch, raw["observedAt"])
    sent = []

    def respond(request):
        sent.append(request)
        assert f"/api/market/{asset.lower()}usd/" in request.url.path
        if request.url.path.endswith("option-catalogue"):
            return httpx.Response(200, json={"underlying": asset, "options": [raw]})
        assert request.url.path.endswith("selected-contracts")
        assert json.loads(request.content) == {"symbols": [raw["symbol"]]}
        return httpx.Response(200, json={"asset": asset, "contracts": [raw]})

    client_type = httpx.Client
    monkeypatch.setattr(recheck.httpx, "Client", lambda **kw: client_type(
        transport=httpx.MockTransport(respond), **kw,
    ))
    original = deepcopy(strategy)
    toolkit = RecheckOptionTools(asset=PROFILES[asset], selected_strategy=strategy)
    assert set(toolkit.functions) == {"preview_selected_strategy"}
    toolkit.functions["preview_selected_strategy"].process_entrypoint()
    assert toolkit.functions["preview_selected_strategy"].parameters["properties"] == {}
    strategy["definition"]["legs"][0]["position"] = "sell"
    result = json.loads(toolkit.preview_selected_strategy())
    assert toolkit.evidence_valid and result["valid"]
    assert result["asset"] == asset and result["netCreditUsd"] == -0.01
    assert result["legs"][0]["position"] == "buy"
    hours = (datetime.fromisoformat(original["exitTime"]) - datetime.fromisoformat(
        original["activationTime"]
    )).total_seconds() / 3600
    scale = result["selectedLegMoveScales"]
    assert scale["hours"] == pytest.approx(hours)
    assert scale["legs"][0]["impliedScaledMovePercent"] == pytest.approx(40 * (hours / 8760)**0.5)
    assert [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in sent] == [
        ("GET", "option-catalogue"), ("POST", "selected-contracts"),
    ]


@pytest.mark.parametrize("fault", ["stale", "missing", "asset", "expiry", "unauthorized", "unavailable", "nonfinite"])
def test_failed_refresh_invalidates_previous_evidence(monkeypatch, fault):
    raw, strategy = assigned()
    freeze(monkeypatch, raw["observedAt"])
    broken = False

    def respond(request):
        if request.url.path.endswith("option-catalogue"):
            return httpx.Response(200, json={"underlying": "BTC", "options": [raw]})
        if broken and fault in {"unauthorized", "unavailable"}:
            return httpx.Response(401 if fault == "unauthorized" else 503)
        quote = deepcopy(raw)
        if broken and fault == "stale":
            quote["observedAt"] -= 31_000
        if broken and fault == "expiry":
            quote["expiry"] = "2026-10-09T12:00:00Z"
        if broken and fault == "nonfinite":
            quote["bidSize"] = "invalid-size"
        return httpx.Response(200, json={
            "asset": "ETH" if broken and fault == "asset" else "BTC",
            "contracts": [] if broken and fault == "missing" else [quote],
        })

    client_type = httpx.Client
    monkeypatch.setattr(recheck.httpx, "Client", lambda **kw: client_type(
        transport=httpx.MockTransport(respond), **kw,
    ))
    toolkit = RecheckOptionTools(asset=PROFILES["BTC"], selected_strategy=strategy)
    if fault == "nonfinite":
        original_preview = recheck.build_preview

        def nonfinite_preview(*args):
            result = original_preview(*args)
            if broken:
                result["legs"][0]["bidSizeContracts"] = float("nan")
            return result

        monkeypatch.setattr(recheck, "build_preview", nonfinite_preview)
    assert json.loads(toolkit.preview_selected_strategy())["valid"]
    broken = True
    assert json.loads(toolkit.preview_selected_strategy())["valid"] is False
    assert toolkit.evidence_valid is False
    broken = False
    assert json.loads(toolkit.preview_selected_strategy())["valid"]
    assert toolkit.evidence_valid is True


def test_assigned_overview_preserves_legacy_expiry_and_excludes_other_maturities():
    packet = {"enrichment": {"options": [
        {"expiry": "2026-10-08T12:00:00Z", "atmIvPercent": 26},
        {"expiry": "2026-10-09T12:00:00Z", "atmIvPercent": 31},
        {"expiry": "invalid"},
    ]}}
    rows = assigned_option_overview(packet, ["2026-10-09"])
    assert rows == [{"expiry": "2026-10-09T12:00:00Z", "atmIvPercent": 31}]
    rows[0]["atmIvPercent"] = 99
    assert packet["enrichment"]["options"][1]["atmIvPercent"] == 31


@pytest.mark.parametrize("curated", [False, True])
@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_main_receives_no_live_option_evidence_and_keeps_private_resolution(monkeypatch, curated, asset):
    monkeypatch.setenv("CURATED_AGENT_INPUT_ENABLED", str(curated).lower())
    raw, strategy = assigned(asset)
    freeze(monkeypatch, raw["observedAt"])
    definition = strategy["definition"]
    saved_id, snapshot_id = str(uuid4()), str(uuid4())
    packet = {
        "capturedAt": raw["observedAt"], "source": "Binance Spot", "symbol": f"{asset}USDT",
        "analysis": {"historicalVolatility": {"annualizedPercent": 40}}, "timeframes": {},
        "enrichment": {"options": [{"atmIvPercent": 99, "expiry": raw["expiry"]}]},
    }
    captured = {}

    def context(*_args, **_kwargs):
        return {"occupied": 0, "snapshot": {
            "id": snapshot_id, "market_json": {
                "executionOptionContext": {"underlying": asset, "options": [raw]},
            },
        }}

    monkeypatch.setattr(tools, "runtime_data", lambda _: SimpleNamespace(request_sync=context))
    monkeypatch.setattr(tools, "LocalResearchClient", lambda *_: SimpleNamespace(
        selection_context=lambda *_: ([{
            "id": saved_id, "version": 1, "enabled_for_ai": True, "user_id": None,
            "name": definition["name"], "definition_json": definition,
        }], {"allocation_mode": "half_balance"}),
    ))
    from automation_agent.market import compact_btc_market_packet

    monkeypatch.setattr(team, "MarketIntelligenceTools", lambda **_: SimpleNamespace(
        collect_market_packet=lambda: packet,
        collect_delta_option_context=lambda: {"underlying": asset, "options": [raw]},
        functions={}, market_packet_json=lambda: json.dumps(compact_btc_market_packet(packet, asset)),
    ))
    monkeypatch.setattr(team, "read_parent_run_context", lambda *_, **__: None)
    monkeypatch.setattr(team, "ChartStorage", lambda _: SimpleNamespace(save_run_charts=lambda **_: []))
    monkeypatch.setattr(team, "save_market_snapshot", lambda *_, **kw: captured.update(
        snapshot=kw["market_packet"]
    ) or snapshot_id)
    monkeypatch.setattr(team, "create_session_db", lambda *_, **__: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(team, "run_news_pipeline", lambda *_, **__: SimpleNamespace(
        markdown="Existing news", report_response=RunOutput(content="Existing news"),
        research_tools=[], research_trace=[],
    ))

    class Agent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            captured["agent"] = self

        async def arun(self, *_, **__):
            return RunOutput(content="No trade")

    monkeypatch.setattr(team, "Agent", Agent)
    team.run_automation_team(
        settings=SimpleNamespace(
            trade_backend_internal_url="http://writer.test", analysis_service_secret="secret",
            automation_model_id="model", require_api_key=lambda: "test",
            automation_session_table="sessions",
        ), user_id="global", agent_run_id=str(uuid4()), session_id="test", account_context={},
        trigger="manual", asset=PROFILES[asset],
    )
    agent = captured["agent"]
    assert "atmIvPercent" not in agent.additional_context
    assert "comparisonBasis" not in agent.additional_context
    assert "holdingMoveScales" not in agent.additional_context
    assert "netCreditUsd" not in agent.additional_context
    assert captured["snapshot"]["executionOptionContext"]["options"] == [raw]
    strategy_tools = agent.tools[0] if curated else agent.tools[1]
    assert "preview_strategy" not in strategy_tools.functions
    assert "calculate_exit_time" in strategy_tools.functions
    if not curated:
        strategy_tools.show_available_strategy()
    schedule = json.loads(strategy_tools.calculate_exit_time(
        "S01", strategy["activationTime"], {"kind": "specific_time", "exit_at": strategy["exitTime"]},
    ))
    assert schedule["valid"] is True
    assert set(schedule) == {"valid", "schedule"}
    assert schedule["schedule"]["exitUtc"] == strategy["exitTime"]


@pytest.mark.parametrize("curated", [False, True])
def test_initial_recheck_evidence_failure_never_invokes_model(monkeypatch, curated):
    monkeypatch.setenv("CURATED_AGENT_INPUT_ENABLED", str(curated).lower())
    _, strategy = assigned()
    monkeypatch.setattr(team, "MarketIntelligenceTools", lambda **_: SimpleNamespace(
        collect_market_packet=lambda: {"timeframes": {}},
    ))

    def failed_read():
        raise ValueError("Fresh selected-contract evidence is unavailable")

    monkeypatch.setattr(team, "RecheckOptionTools", lambda **_: SimpleNamespace(collect_evidence=failed_read))
    monkeypatch.setattr(team, "Agent", lambda **_: pytest.fail("Model cannot approve a failed evidence read"))
    with pytest.raises(ValueError, match="Fresh"):
        team.run_activation_recheck(
            settings=SimpleNamespace(), user_id="global", agent_run_id=str(uuid4()), session_id="test",
            recheck_context={"proposalId": str(uuid4()), "selectedStrategy": strategy},
        )


@pytest.mark.parametrize("fault", [None, "arguments", "version", "snapshot", "recalculation"])
def test_compact_selection_uses_validated_schedule_and_rejects_changed_evidence(monkeypatch, fault):
    raw, strategy = assigned()
    frozen_datetime = freeze(monkeypatch, raw["observedAt"])
    monkeypatch.setattr(automation_schedule, "datetime", frozen_datetime)
    saved_id, snapshot_id = str(uuid4()), str(uuid4())
    row = {
        "id": saved_id, "version": 1, "enabled_for_ai": True, "name": strategy["name"],
        "definition_json": strategy["definition"], "user_id": None,
    }
    snapshot = {
        "id": snapshot_id, "market_json": {"executionOptionContext": {"underlying": "BTC", "options": [raw]}},
    }
    published = []

    def request(path, args, *, mutation=False):
        if path == "runtimeAutomation:context":
            assert not mutation
            return {"snapshot": snapshot, "occupied": 0}
        assert path == "sharedAnalysis:publish" and mutation
        published.append(args)
        return {"outcome": "strategy_selected"}

    monkeypatch.setattr(tools, "runtime_data", lambda _: SimpleNamespace(request_sync=request))
    monkeypatch.setattr(tools, "LocalResearchClient", lambda *_: SimpleNamespace(
        selection_context=lambda *_: ([row], {"allocation_mode": "half_balance"}),
    ))
    toolkit = tools.AutomationStrategyTools(
        SimpleNamespace(trade_backend_internal_url="http://writer.test", analysis_service_secret="secret"),
        user_id="global", agent_run_id=str(uuid4()), market_snapshot_id=snapshot_id, curated=True,
    )
    toolkit.starting_catalogue()
    choice = {"kind": "specific_time", "exit_at": strategy["exitTime"]}
    calculation = json.loads(toolkit.calculate_exit_time("S01", strategy["activationTime"], choice))
    assert calculation["valid"] is True
    activation = strategy["activationTime"]
    errors = {"arguments": "calculate_exit_time", "version": "changed", "snapshot": "snapshot",
              "recalculation": "calculate_exit_time"}
    if fault == "arguments":
        activation = (datetime.fromisoformat(activation) + timedelta(minutes=1)).isoformat()
    elif fault == "version":
        row["version"] += 1
    elif fault == "snapshot":
        snapshot["id"] = str(uuid4())
    elif fault == "recalculation":
        snapshot["market_json"]["executionOptionContext"]["options"] = []
        assert json.loads(toolkit.calculate_exit_time("S01", activation, choice))["valid"] is False

    def select():
        return toolkit._select_strategy_and_time(
            saved_id=saved_id, saved_strategy_version=1, activation_time=activation,
            ai_confidence=0.7, reasoning_summary="Spot setup holds", supporting_signals=["spot"],
            invalidation_signals=["range break"], exit_choice=choice,
        )

    if fault:
        with pytest.raises(ValueError, match=errors[fault]):
            select()
        assert published == []
    else:
        assert json.loads(select())["outcome"] == "strategy_selected"
        assert published[0]["exit"] == strategy["exitTime"]
        live = json.loads(published[0]["definitionJson"])
        assert live["legs"] == strategy["definition"]["legs"]
        assert live["stopLossPercent"] == strategy["definition"]["stopLossPercent"]
