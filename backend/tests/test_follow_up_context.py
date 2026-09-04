import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from agno.run.team import TeamRunOutput

from automation_agent import team, tools

USER = "11111111-1111-4111-8111-111111111111"
PARENT = "22222222-2222-4222-8222-222222222222"
CHILD = "33333333-3333-4333-8333-333333333333"
SNAPSHOT = "44444444-4444-4444-8444-444444444444"
SETTINGS = SimpleNamespace(require_database_url=lambda: "postgresql://unused")


@pytest.mark.parametrize("path", ["create", "reschedule", "reuse"])
def test_scheduling_persists_parent_in_the_same_transaction(monkeypatch, path):
    now = datetime.now(UTC)
    current = {"enabled": True, "trigger": "asia_session", "minimum_follow_up_minutes": 5,
               "maximum_agent_runs_per_day": 3}
    existing = {"id": CHILD, "trigger": "agent_follow_up", "scheduled_for": now + timedelta(minutes=8)}
    answers = [current, existing, {"id": PARENT}] if path == "reuse" else [
        current, None, {"count": 0}, {"id": CHILD} if path == "reschedule" else None,
        *([] if path == "reschedule" else [{"count": 0}]),
        {"id": PARENT}, *([] if path == "reschedule" else [{"id": CHILD}]),
    ]
    responses = iter(answers)
    statements = []
    commits = []

    class Cursor:
        def execute(self, sql, args):
            assert sql.count("%s") == len(args)
            statements.append((sql, args))

        def fetchone(self):
            return next(responses)

    connection = SimpleNamespace(cursor=lambda: nullcontext(Cursor()), commit=lambda: commits.append(True))
    toolkit = tools.AutomationStrategyTools(SETTINGS, user_id=USER, agent_run_id=PARENT, market_snapshot_id=SNAPSHOT)
    monkeypatch.setattr(toolkit, "_connect", lambda: nullcontext(connection))
    monkeypatch.setattr(tools, "next_fixed_run", lambda _: SimpleNamespace(scheduled_for=now + timedelta(hours=1)))
    monkeypatch.setattr(tools, "previous_fixed_run", lambda _: SimpleNamespace(scheduled_for=now - timedelta(hours=1)))

    result = json.loads(toolkit.scheduled_next_agent_run(
        (now + timedelta(minutes=10)).isoformat(), "Wait for confirmation", ["range breakout"]
    ))

    assert result["scheduledRunId"] == CHILD
    assert commits == [True]
    writes = [(sql, args) for sql, args in statements if "parent_agent_run_id" in sql]
    assert len(writes) == 1
    sql, args = writes[0]
    assert PARENT in args
    if path == "reuse":
        assert "parent_agent_run_id is null" in sql
        assert args == (PARENT, CHILD, USER)
    elif path == "reschedule":
        assert args[-2:] == (PARENT, CHILD)
    else:
        assert args[-1] == PARENT
        assert "parent_agent_run_id = excluded.parent_agent_run_id" in sql


@pytest.mark.parametrize("record", [None, {"runId": PARENT, "finalResponse": "Full report\n" * 3000}])
def test_parent_lookup_is_exact_user_scoped_and_does_not_truncate(monkeypatch, record):
    def execute(sql, args):
        assert "parent.id = child.parent_agent_run_id" in sql
        assert "parent.user_id = child.user_id" in sql
        assert args == (CHILD, USER)
        return SimpleNamespace(fetchone=lambda: record)

    monkeypatch.setattr(tools.psycopg, "connect", lambda *_, **__: nullcontext(SimpleNamespace(execute=execute)))
    assert tools.read_parent_run_context(SETTINGS, user_id=USER, agent_run_id=CHILD) == record


@pytest.mark.parametrize("previous", [None, {"runId": PARENT, "finalResponse": "Earlier complete assessment"}])
def test_main_team_receives_parent_report_and_fresh_market_context(monkeypatch, previous):
    captured = {}
    monkeypatch.setattr(team, "read_parent_run_context", lambda *_, **__: previous)
    monkeypatch.setattr(team, "MarketIntelligenceTools", lambda: SimpleNamespace(
        collect_btc_market_packet=lambda: {"source": "Binance Spot"}, collect_delta_option_context=lambda: {},
    ))
    monkeypatch.setattr(team, "SupabaseChartStorage", lambda _: SimpleNamespace(upload_run_charts=lambda **_: []))
    monkeypatch.setattr(team, "_chart_artifacts", lambda _: [
        SimpleNamespace(id="btc-test", context={"readingNotes": ["Main chart instructions"]})
    ])
    monkeypatch.setattr(team, "save_market_snapshot", lambda *_, **__: SNAPSHOT)
    monkeypatch.setattr(team, "AutomationStrategyTools", lambda *_, **__: object())
    monkeypatch.setattr(team, "create_session_db", lambda *_, **__: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(team, "create_news_agent", lambda **_: object())

    class Team:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, *_, **__):
            return TeamRunOutput(content="## Decision\nNew decision.")

    monkeypatch.setattr(team, "Team", Team)
    account = {"activeStrategies": []}
    team.run_automation_team(
        settings=SimpleNamespace(automation_session_table="sessions", automation_model_id="model",
                                 require_api_key=lambda: "test-key"),
        user_id=USER, agent_run_id=CHILD, session_id="test", account_context=account,
        trigger="agent_follow_up", trigger_reason="Wait for confirmation", signals_to_inspect=["breakout"],
    )
    assert "Wait for confirmation" in captured["additional_context"]
    assert "Main chart instructions" in captured["additional_context"]
    assert "breakout" in captured["additional_context"]
    assert ("Earlier complete assessment" in captured["additional_context"]) == bool(previous)
    assert account == {"activeStrategies": []}
