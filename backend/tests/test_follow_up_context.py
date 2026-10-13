import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from agno.run.agent import RunOutput

from automation_agent import team, tools

USER = "11111111-1111-4111-8111-111111111111"
PARENT = "22222222-2222-4222-8222-222222222222"
CHILD = "33333333-3333-4333-8333-333333333333"
SNAPSHOT = "44444444-4444-4444-8444-444444444444"
SETTINGS = SimpleNamespace(trade_backend_internal_url="http://writer.test", analysis_service_secret="secret")


def test_scheduling_sends_parent_run_and_snapshot_to_the_writer(monkeypatch):
    now = datetime.now(UTC)
    sent = {}

    def request(path, args, *, mutation=False):
        sent.update(path=path, args=args, mutation=mutation)
        return {"outcome": "wait_and_run_again", "scheduledRunId": CHILD, "nextRunTime": args["next"]}

    monkeypatch.setattr(tools, "runtime_data", lambda _: SimpleNamespace(request_sync=request))
    monkeypatch.setattr(tools, "read_automation_state", lambda *_args, **_kwargs: {"outcome": None})
    monkeypatch.setattr(tools, "next_fixed_run", lambda _: SimpleNamespace(scheduled_for=now + timedelta(hours=1)))
    monkeypatch.setattr(tools, "previous_fixed_run", lambda _: SimpleNamespace(scheduled_for=now - timedelta(hours=1)))
    toolkit = tools.AutomationStrategyTools(SETTINGS, user_id=USER, agent_run_id=PARENT, market_snapshot_id=SNAPSHOT)

    result = json.loads(
        toolkit.scheduled_next_agent_run(
            (now + timedelta(minutes=10)).isoformat(), "Wait for confirmation", ["range breakout", " "]
        )
    )

    assert result["scheduledRunId"] == CHILD and result["status"] == "committed"
    assert sent["path"] == "runtimeAutomation:followup" and sent["mutation"] is True
    assert sent["args"]["runId"] == PARENT and sent["args"]["userId"] == USER
    assert sent["args"]["snapshotId"] == SNAPSHOT
    assert sent["args"]["signals"] == ["range breakout"]


@pytest.mark.parametrize(
    "parent",
    [
        None,
        {
            "id": PARENT,
            "scheduled_for": "2026-09-15T00:00:00Z",
            "trigger": "asia_session",
            "outcome": "wait_and_run_again",
            "report_markdown": "Full report\n" * 3000,
        },
    ],
)
def test_parent_lookup_uses_the_exact_parent_and_does_not_truncate(monkeypatch, parent):
    def request(path, args, **_):
        assert path == "runtimeAutomation:context"
        assert args == {"userId": USER, "runId": CHILD}
        return {"run": {"id": CHILD}, "parent": parent}

    monkeypatch.setattr(tools, "runtime_data", lambda _: SimpleNamespace(request_sync=request))
    result = tools.read_parent_run_context(SETTINGS, user_id=USER, agent_run_id=CHILD)
    if parent is None:
        assert result is None
    else:
        assert result["runId"] == PARENT
        assert result["finalResponse"] == parent["report_markdown"]


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
@pytest.mark.parametrize("previous", [None, {"runId": PARENT, "finalResponse": "Earlier complete assessment"}])
def test_main_team_receives_parent_report_and_fresh_market_context(monkeypatch, previous, asset):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 13, 23, 59, 58, tzinfo=UTC)

    monkeypatch.setattr(team, "datetime", FrozenDatetime)
    captured = {}
    monkeypatch.setattr(team, "read_parent_run_context", lambda *_, **__: previous)
    monkeypatch.setattr(
        team,
        "MarketIntelligenceTools",
        lambda **_: SimpleNamespace(
            collect_market_packet=lambda: {"source": "Binance Spot", "capturedAt": 1791417600000},
            collect_delta_option_context=lambda: {},
        ),
    )
    monkeypatch.setattr(team, "ChartStorage", lambda _: SimpleNamespace(save_run_charts=lambda **_: []))
    monkeypatch.setattr(
        team,
        "_chart_artifacts",
        lambda *_: [SimpleNamespace(id="btc-test", context={"readingNotes": ["Main chart instructions"]})],
    )
    monkeypatch.setattr(team, "save_market_snapshot", lambda *_, **__: SNAPSHOT)
    monkeypatch.setattr(team, "AutomationStrategyTools", lambda *_, **__: object())
    monkeypatch.setattr(team, "create_session_db", lambda *_, **__: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        team,
        "run_news_pipeline",
        lambda *_, **__: SimpleNamespace(
            markdown="Current verified news",
            research_tools=["search_news", "build_news_dossier"],
            research_trace=[],
            report_response=RunOutput(content="Current verified news"),
        ),
    )

    class Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def arun(self, *_, **__):
            return RunOutput(content="## Decision\nNew decision.")

    monkeypatch.setattr(team, "Agent", Agent)
    account = {"activeStrategies": []}
    team.run_automation_team(
        settings=SimpleNamespace(
            automation_session_table="sessions", automation_model_id="model", require_api_key=lambda: "test-key"
        ),
        user_id=USER,
        agent_run_id=CHILD,
        session_id="test",
        account_context=account,
        trigger="agent_follow_up",
        trigger_reason="Wait for confirmation",
        signals_to_inspect=["breakout"],
        asset=team.PROFILES[asset],
    )
    assert captured["instructions"][0].startswith(f"Assess {asset}USD direction")
    assert captured["name"] == f"{asset} Automation agent"
    assert captured["id"] == team.PROFILES[asset].agent_id
    assert len(captured["instructions"]) == 9
    assert all("Only at-the-money" not in text and "moves more than BTC" not in text
               for text in captured["instructions"])
    assert captured["model"].reasoning_effort == "max"
    assert "max_tokens" not in captured["model"].get_request_params()
    assert "max_completion_tokens" not in captured["model"].get_request_params()
    assert captured["additional_context"].startswith("Run started at: 2026-10-13T23:59:58+00:00 (UTC).")
    schedule_text = captured["additional_context"].split("Next three permanent agent runs: ", 1)[1]
    permanent_runs, _ = json.JSONDecoder().raw_decode(schedule_text)
    assert len(permanent_runs) == 3
    assert permanent_runs[0]["trigger"] == "asia_session"
    assert permanent_runs[0]["scheduledForIst"] == "2026-10-14T05:30:00+05:30"
    assert all(datetime.fromisoformat(row["scheduledForUtc"].replace("Z", "+00:00")) >
               FrozenDatetime.now(UTC) for row in permanent_runs)
    assert all("calculate_exit_time" not in text for text in captured["instructions"])
    assert "Current verified news" in captured["additional_context"]
    assert "Wait for confirmation" in captured["additional_context"]
    assert "Main chart instructions" in captured["additional_context"]
    assert "breakout" in captured["additional_context"]
    assert ("Earlier complete assessment" in captured["additional_context"]) == bool(previous)
    assert account == {"activeStrategies": []}
