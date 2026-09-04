import json
from contextlib import nullcontext
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


@pytest.mark.parametrize("drop_attempt,committed", [(False, False), (True, True), (True, False)])
def test_recheck_never_reconfirms_a_failed_drop(monkeypatch, drop_attempt, committed):
    state = {"outcome": "strategy_dropped" if committed else None}
    result = SimpleNamespace(
        run_id="run", session_id="session", model_id="model", report="Decision",
        market_snapshot_id="snapshot", member_responses=[],
        tool_calls=[{"name": "drop_strategy"}] if drop_attempt else [],
    )
    monkeypatch.setattr(main, "run_activation_recheck", lambda **_: result)
    monkeypatch.setattr(main, "read_automation_state", lambda *_, **__: state)
    monkeypatch.setattr(
        main, "run_automation_team", lambda **_: pytest.fail("Rechecks must not run the main team")
    )

    def confirm(*_, **__):
        assert not drop_attempt
        state["outcome"] = "strategy_reconfirmed"

    monkeypatch.setattr(main, "confirm_activation_recheck", confirm)
    if drop_attempt and not committed:
        with pytest.raises(main.ServiceError):
            main._run_automation_analysis(recheck_request(), "test")
        assert state["outcome"] is None
    else:
        response = main._run_automation_analysis(recheck_request(), "test")
        assert response["outcome"] == ("strategy_dropped" if committed else "strategy_reconfirmed")


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


@pytest.mark.parametrize("name,time,expected_error", [
    ("Short strangle", ACTIVATION.isoformat(), None),
    ("Long call", ACTIVATION.isoformat(), "strategy_name"),
    ("Short strangle", "2026-09-03T07:02:00Z", "activation_time"),
])
def test_drop_validates_the_bound_strategy_before_cancelling(monkeypatch, name, time, expected_error):
    statements = []
    results = iter([{
        "proposal_status": "scheduled", "strategy_status": "scheduled", "strategy_id": "strategy-1",
        "name": "Short strangle", "activation_time": ACTIVATION,
    }, {"id": RUN_ID}])

    class Cursor:
        def execute(self, sql, args):
            statements.append((sql, args))

        def fetchone(self):
            return next(results)

    connection = SimpleNamespace(cursor=lambda: nullcontext(Cursor()), commit=lambda: None)
    monkeypatch.setattr(tools.psycopg, "connect", lambda *_, **__: nullcontext(connection))
    toolkit = tools.DropStrategyTools(
        SimpleNamespace(require_database_url=lambda: "postgresql://unused"),
        user_id=USER_ID, agent_run_id=RUN_ID, proposal_id=PROPOSAL_ID,
    )
    assert set(toolkit.functions) == {"drop_strategy"}
    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            toolkit.drop_strategy(name, time, "Range broke")
        assert len(statements) == 1
    else:
        result = json.loads(toolkit.drop_strategy(name, time, "Range broke"))
        assert result["outcome"] == "strategy_dropped"
        assert statements[0][1] == (PROPOSAL_ID, USER_ID)
        assert statements[1][1] == (RUN_ID, USER_ID, PROPOSAL_ID)
        assert statements[2][1][1] == "strategy-1"
        assert statements[3][1][1] == PROPOSAL_ID


def test_market_tool_and_charts_exclude_delta():
    toolkit = MarketIntelligenceTools()
    assert set(toolkit.functions) == {"get_btc_market_packet"}
    assert team._chart_artifacts({"deltaExecutionContext": {"openInterestHistory": [{"close": 1}] * 3}}) == []


def test_recheck_has_fresh_charts_and_no_news_or_strategy_selection_tools(monkeypatch):
    captured = {}
    packet = {"source": "Binance Spot", "timeframes": {}}
    monkeypatch.setattr(team, "MarketIntelligenceTools", lambda: SimpleNamespace(
        collect_btc_market_packet=lambda: packet,
        get_btc_market_packet=lambda: json.dumps(packet),
    ))
    charts = [StoredChart("btc-1-minute", "Fresh price", "Fresh Binance chart", "charts", "test.png",
                          "https://example.com/test.png")]
    monkeypatch.setattr(team, "_recheck_chart_artifacts", lambda _: [
        SimpleNamespace(id="btc-1-minute", context={"readingNotes": ["Recheck chart instructions"]})
    ])
    monkeypatch.setattr(team, "SupabaseChartStorage", lambda _: SimpleNamespace(upload_run_charts=lambda **_: charts))
    monkeypatch.setattr(team, "save_market_snapshot", lambda *_, **__: "snapshot")
    monkeypatch.setattr(team, "create_news_agent", lambda **_: pytest.fail("Recheck created a news agent"))
    monkeypatch.setattr(team, "Team", lambda **_: pytest.fail("Recheck created a team"))

    class Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, prompt, **kwargs):
            captured["input"] = kwargs
            return RunOutput(content="## Decision\n\nGo.")

    monkeypatch.setattr(team, "Agent", Agent)
    result = team.run_activation_recheck(
        settings=SimpleNamespace(automation_model_id="model", require_api_key=lambda: "test-key",
                                 require_database_url=lambda: "postgresql://unused"),
        user_id=USER_ID, agent_run_id=RUN_ID, session_id="test",
        recheck_context={"proposalId": PROPOSAL_ID,
                         "selectedStrategy": {"name": "Short strangle", "activationTime": ACTIVATION.isoformat()},
                         "originalSelection": {"finalResponse": "Exact original report"}},
    )
    assert "Exact original report" in captured["additional_context"]
    assert "Recheck chart instructions" in captured["additional_context"]
    assert len(captured["tools"]) == 1
    assert set(captured["tools"][0].functions) == {"drop_strategy"}
    assert captured["input"]["images"][0].url == charts[0].signed_url
    assert result.member_responses == []
