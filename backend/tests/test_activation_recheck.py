import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from agno.run.agent import RunOutput

from app.automation import build_activation_recheck_context
from app.errors import AppError
from automation_agent import team, tools
from automation_agent.market import MarketIntelligenceTools
from automation_agent.storage import StoredChart
from news_analyzer import main

USER_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
PROPOSAL_ID = "33333333-3333-4333-8333-333333333333"
ACTIVATION = datetime(2026, 9, 3, 7, 1, tzinfo=UTC)


def recheck_request():
    return main.AutomationAnalysisRequest(
        userId=USER_ID,
        agentRunId=RUN_ID,
        sessionId="scheduled-recheck",
        accountContext={"proposalId": PROPOSAL_ID},
        trigger="activation_recheck",
    )


@pytest.mark.parametrize("read_tools", [[], [{"name": "multiply"}], [{"name": "preview_selected_strategy"}]])
@pytest.mark.parametrize("action,committed", [
    (None, False), ("approve_strategy", True), ("approve_strategy", False),
    ("drop_strategy", True), ("drop_strategy", False),
])
def test_recheck_requires_a_committed_explicit_decision(monkeypatch, action, committed, read_tools):
    expected = "strategy_reconfirmed" if action == "approve_strategy" else "strategy_dropped"
    state = {"outcome": expected if committed else None}
    result = SimpleNamespace(
        run_id="run", session_id="session", model_id="model", report="Decision",
        market_snapshot_id="snapshot", member_responses=[],
        tool_calls=read_tools + ([{"name": action}] if action else []),
    )
    monkeypatch.setattr(main, "run_activation_recheck", lambda **_: result)
    monkeypatch.setattr(main, "read_automation_state", lambda *_, **__: state)
    monkeypatch.setattr(
        main, "run_automation_team", lambda **_: pytest.fail("Rechecks must not run the main team")
    )

    if not committed:
        with pytest.raises(main.ServiceError):
            main._run_automation_analysis(recheck_request(), "test")
        assert state["outcome"] is None
    else:
        response = main._run_automation_analysis(recheck_request(), "test")
        assert response["outcome"] == expected


@pytest.mark.parametrize("report", ["Go", "Inconclusive", '{"decision":"approve","unfinished'])
def test_plain_reports_cannot_approve_without_a_tool(monkeypatch, report):
    result = SimpleNamespace(report=report, tool_calls=[])
    monkeypatch.setattr(main, "run_activation_recheck", lambda **_: result)
    monkeypatch.setattr(main, "read_automation_state", lambda *_, **__: {"outcome": None})
    with pytest.raises(main.ServiceError):
        main._run_automation_analysis(recheck_request(), "test")


@pytest.mark.parametrize("missing_report", [False, True])
async def test_recheck_context_reads_only_its_strategy_and_original_run(missing_report):
    class Database:
        async def select(self, table, query):
            assert query["user_id"] == f"eq.{USER_ID}"
            if table == "strategy_proposals":
                assert query["id"] == f"eq.{PROPOSAL_ID}"
                return [{
                    "id": PROPOSAL_ID, "agent_run_id": RUN_ID, "strategy_id": "strategy-1",
                    "status": "scheduled", "activation_time": ACTIVATION.isoformat(),
                    "proposal_expiry": ACTIVATION.isoformat(), "ai_confidence": 0.7,
                    "reasoning_summary": "Range holding",
                }]
            if table == "strategies":
                assert query["id"] == "eq.strategy-1"
                return [{"id": "strategy-1", "name": "Short strangle", "status": "scheduled",
                         "exit_at": ACTIVATION.isoformat(), "definition_json": {"name": "Short strangle"}}]
            assert table == "automation_agent_runs"
            assert query["id"] == f"eq.{RUN_ID}"
            return [{"id": RUN_ID, "report_markdown": None if missing_report else "Original complete report"}]

    if missing_report:
        with pytest.raises(AppError, match="final report is unavailable"):
            await build_activation_recheck_context(Database(), USER_ID, PROPOSAL_ID)
    else:
        context = await build_activation_recheck_context(Database(), USER_ID, PROPOSAL_ID)
        assert context["originalSelection"]["finalResponse"] == "Original complete report"
        assert context["selectedStrategy"]["id"] == "strategy-1"
        assert "upcomingAgentRuns" not in context


@pytest.mark.parametrize("action,drop,outcome", [
    ("approve_strategy", False, "strategy_reconfirmed"), ("drop_strategy", True, "strategy_dropped"),
])
def test_decision_sends_the_bound_proposal_to_the_writer(monkeypatch, action, drop, outcome):
    sent = {}

    def request(path, args, *, mutation=False):
        sent.update(path=path, args=args, mutation=mutation)
        return {"outcome": outcome, "strategy": "Short strangle"}

    monkeypatch.setattr(tools, "runtime_data", lambda _: SimpleNamespace(request_sync=request))
    toolkit = tools.ActivationRecheckTools(
        SimpleNamespace(), user_id=USER_ID, agent_run_id=RUN_ID, proposal_id=PROPOSAL_ID,
        evidence_is_valid=lambda: True,
    )
    assert set(toolkit.functions) == {"approve_strategy", "drop_strategy"}
    decision = getattr(toolkit, action)
    toolkit.functions[action].process_entrypoint()
    assert set(toolkit.functions[action].parameters["properties"]) == {"strategy_name", "activation_time", "reason"}
    with pytest.raises(ValueError, match="reason"):
        decision("Short strangle", ACTIVATION.isoformat(), " ")
    with pytest.raises(ValueError, match="activation_time"):
        decision("Short strangle", "2026-09-03T07:02:00", "Range broke")
    result = json.loads(decision("Short strangle", ACTIVATION.isoformat(), "Reviewed setup"))
    assert result["outcome"] == outcome
    assert sent == {"path": "runtimeAutomation:recheck", "mutation": True, "args": {
        "userId": USER_ID, "runId": RUN_ID, "proposalId": PROPOSAL_ID, "drop": drop,
        "name": "Short strangle", "activation": ACTIVATION.isoformat(), "reason": "Reviewed setup",
    }}


def test_failed_evidence_refresh_blocks_approval_but_allows_drop(monkeypatch):
    mutations = []
    evidence = SimpleNamespace(valid=True)

    def request(path, args, *, mutation=False):
        mutations.append(args)
        return {"outcome": "strategy_dropped" if args["drop"] else "strategy_reconfirmed"}

    monkeypatch.setattr(tools, "runtime_data", lambda _: SimpleNamespace(request_sync=request))
    toolkit = tools.ActivationRecheckTools(
        SimpleNamespace(), user_id=USER_ID, agent_run_id=RUN_ID, proposal_id=PROPOSAL_ID,
        evidence_is_valid=lambda: evidence.valid,
    )
    evidence.valid = False
    with pytest.raises(ValueError, match="Cannot approve"):
        toolkit.approve_strategy("Short strangle", ACTIVATION.isoformat(), "Earlier quotes were valid")
    assert mutations == []
    assert json.loads(toolkit.drop_strategy(
        "Short strangle", ACTIVATION.isoformat(), "Refresh failed",
    ))["outcome"] == "strategy_dropped"
    assert mutations[0]["drop"] is True


def test_market_tool_and_charts_exclude_delta():
    toolkit = MarketIntelligenceTools()
    assert set(toolkit.functions) == {"get_btc_market_packet"}
    assert team._chart_artifacts({"deltaExecutionContext": {"openInterestHistory": [{"close": 1}] * 3}}) == []


@pytest.mark.parametrize("report", [
    "## Decision\n\nGo. The setup still holds.",
    "## Decision\n\nInconclusive. Fresh evidence is limited.",
    '{"decision": "go", "report": "unfinished',
])
@pytest.mark.parametrize("evidence_valid", [True, False])
@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_recheck_preserves_report_and_exposes_both_decision_tools(monkeypatch, report, evidence_valid, asset):
    captured = {}
    packet = {"source": "Binance Spot", "timeframes": {}}
    monkeypatch.setattr(team, "MarketIntelligenceTools", lambda **_: SimpleNamespace(
        collect_market_packet=lambda: packet,
        market_packet_json=lambda: json.dumps(packet),
        assigned_expiries=[],
    ))
    charts = [StoredChart("btc-1-minute", "Fresh price", "Fresh Binance chart", RUN_ID, b"png-bytes")]
    monkeypatch.setattr(team, "_recheck_chart_artifacts", lambda *_: [
        SimpleNamespace(id="btc-1-minute", context={"readingNotes": ["Recheck chart instructions"]})
    ])
    monkeypatch.setattr(team, "ChartStorage", lambda _: SimpleNamespace(save_run_charts=lambda **_: charts))
    monkeypatch.setattr(team, "save_market_snapshot", lambda *_, **__: "snapshot")
    monkeypatch.setattr(team, "run_news_pipeline", lambda *_, **__: pytest.fail("Recheck researched news"))
    monkeypatch.setattr(team, "RecheckOptionTools", lambda **_: SimpleNamespace(
        collect_evidence=lambda: {"valid": True, "netCreditUsd": 1.25},
        evidence_valid=evidence_valid, functions={"preview_selected_strategy": None},
    ))

    class Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, prompt, **kwargs):
            captured["input"] = kwargs
            return RunOutput(content=report)

    monkeypatch.setattr(team, "Agent", Agent)
    kwargs = dict(
        settings=SimpleNamespace(automation_model_id="model", require_api_key=lambda: "test-key",
                                 require_database_url=lambda: "postgresql://unused",
                                 trade_backend_internal_url="http://writer.test", analysis_service_secret="secret"),
        user_id=USER_ID, agent_run_id=RUN_ID, session_id="test",
        recheck_context={"proposalId": PROPOSAL_ID,
                         "selectedStrategy": {"name": "Short strangle", "activationTime": ACTIVATION.isoformat()},
                         "originalSelection": {"finalResponse": "Exact original report"}},
        asset=team.PROFILES[asset],
    )
    if not evidence_valid:
        with pytest.raises(RuntimeError, match="cannot approve"):
            team.run_activation_recheck(**kwargs)
        return
    result = team.run_activation_recheck(**kwargs)
    assert captured["id"] == team.PROFILES[asset].recheck_agent_id
    assert captured["name"] == f"{asset} Strategy Activation Recheck"
    assert len(captured["instructions"]) == 8
    assert all("Only at-the-money" not in text and "moves more than BTC" not in text
               for text in captured["instructions"])
    assert captured["model"].reasoning_effort == "low"
    assert "max_tokens" not in captured["model"].get_request_params()
    assert "max_completion_tokens" not in captured["model"].get_request_params()
    assert captured["model"].timeout == 240
    assert "Exact original report" in captured["additional_context"]
    assert "Recheck chart instructions" in captured["additional_context"]
    assert len(captured["tools"]) == 3
    assert set(captured["tools"][0].functions) == {"approve_strategy", "drop_strategy"}
    assert "call approve_strategy" in " ".join(captured["instructions"])
    assert "too risky or ineligible" in " ".join(captured["instructions"])
    assert "with valid evidence and no drop" not in " ".join(captured["instructions"])
    assert set(captured["tools"][1].functions) == {"preview_selected_strategy"}
    assert captured["tool_call_limit"] == 16
    assert "netCreditUsd" in captured["additional_context"]
    assert "output_schema" not in captured
    assert captured["input"]["images"][0].content == b"png-bytes"
    assert captured["input"]["images"][0].url is None
    assert result.member_responses == []
    assert result.tool_calls == []
    assert result.report == report
