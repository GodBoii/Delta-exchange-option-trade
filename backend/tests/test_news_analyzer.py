from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from news_agent.config import NewsAgentSettings
from news_agent.database import create_session_db
from news_analyzer import main


@pytest.mark.parametrize("trigger", ["pre_expiry", "midnight_review"])
def test_automation_request_accepts_new_fixed_triggers(trigger: str) -> None:
    request = main.AutomationAnalysisRequest(
        userId="11111111-1111-4111-8111-111111111111",
        agentRunId="22222222-2222-4222-8222-222222222222",
        sessionId="scheduled-fixed-review",
        accountContext={},
        trigger=trigger,
    )

    assert request.trigger == trigger


def test_session_database_factory_uses_agno_postgres(monkeypatch) -> None:
    settings = replace(
        NewsAgentSettings.load(),
        database_url="postgresql://trade_ai:secret@trade-postgres:5432/trade_cognition",
    )
    captured: dict = {}
    fake_db = SimpleNamespace()

    def fake_postgres(**kwargs):
        captured.update(kwargs)
        return fake_db

    monkeypatch.setattr("news_agent.database.PostgresDb", fake_postgres)

    assert create_session_db(settings) is fake_db
    assert captured["db_schema"] == settings.db_schema
    assert captured["session_table"] == settings.session_table
    assert captured["create_schema"] is settings.db_create_schema
    parsed_url = urlsplit(captured["db_url"])
    assert parsed_url.scheme == "postgresql+psycopg"
    assert parsed_url.hostname == "trade-postgres"
    assert parse_qs(parsed_url.query)["keepalives"] == ["1"]
    assert "connect_timeout" not in parse_qs(parsed_url.query)


@pytest.mark.asyncio
async def test_health_reports_local_postgres(monkeypatch) -> None:
    monkeypatch.setattr(main, "settings", replace(main.settings, database_url="postgresql+psycopg://configured"))
    monkeypatch.setattr(main, "_database_status", lambda: (True, None))
    response = await main.health()
    assert response["service"] == "news-analyzer"
    assert response["database"] == "local-postgres"
    assert response["databaseConfigured"] is True
    assert response["databaseReady"] is True
    assert response["sessionTable"] == main.settings.session_table


def test_database_connection_failure_is_reported_and_connection_closed(monkeypatch) -> None:
    closed: list[bool] = []
    fake_db = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(main, "create_session_db", lambda _: fake_db)
    monkeypatch.setattr(main, "verify_session_db", lambda _: (_ for _ in ()).throw(OSError("connection failed")))

    ready, error = main._database_status()

    assert ready is False
    assert error is not None and "cannot connect" in error
    assert closed == [True]


def test_automation_uses_committed_outcome_instead_of_response_tool_list(monkeypatch) -> None:
    result = SimpleNamespace(
        run_id="agno-run",
        session_id="automation:user:run",
        model_id="model-a",
        report="## Decision\n\nNo trade.",
        market_snapshot_id="snapshot-1",
        member_responses=[],
        tool_calls=[{"name": "scheduled_next_agent_run"}],
    )
    monkeypatch.setattr(main, "run_automation_team", lambda **_kwargs: result)
    monkeypatch.setattr(
        main,
        "read_automation_state",
        lambda *_args, **_kwargs: {"outcome": "strategy_selected", "market_snapshot_id": "snapshot-1"},
    )
    body = main.AutomationAnalysisRequest(
        userId="11111111-1111-4111-8111-111111111111",
        agentRunId="22222222-2222-4222-8222-222222222222",
        sessionId="scheduled-run",
        accountContext={},
        trigger="asia_session",
    )

    response = main._run_automation_analysis(body, "trace-1")

    assert response["outcome"] == "strategy_selected"
    assert "A strategy was scheduled for this account" in response["report"]


def test_automation_report_replaces_uncommitted_selection(monkeypatch) -> None:
    result = SimpleNamespace(
        run_id="agno-run", session_id="automation:user:run", model_id="model-a",
        report="## Market regime\n\nSideways.\n\n## Decision\n\nScheduled a strangle.",
        market_snapshot_id="snapshot-1", member_responses=[], tool_calls=[],
    )
    monkeypatch.setattr(main, "run_automation_team", lambda **_kwargs: result)
    monkeypatch.setattr(
        main, "read_automation_state",
        lambda *_args, **_kwargs: {"outcome": None, "market_snapshot_id": "snapshot-1"},
    )
    body = main.AutomationAnalysisRequest(
        userId="global", agentRunId="22222222-2222-4222-8222-222222222222",
        sessionId="scheduled-run", accountContext={}, trigger="asia_session",
    )

    response = main._run_automation_analysis(body, "trace-1")

    assert response["outcome"] == "no_trade_for_current_window"
    assert "Scheduled a strangle" not in response["report"]
    assert "No strategy was scheduled during this review" in response["report"]


def test_automation_recovers_committed_action_when_final_report_fails(monkeypatch) -> None:
    monkeypatch.setattr(main, "run_automation_team", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("provider")))
    monkeypatch.setattr(
        main,
        "read_automation_state",
        lambda *_args, **_kwargs: {"outcome": "wait_and_run_again", "market_snapshot_id": "snapshot-1"},
    )
    body = main.AutomationAnalysisRequest(
        userId="11111111-1111-4111-8111-111111111111",
        agentRunId="22222222-2222-4222-8222-222222222222",
        sessionId="scheduled-run",
        accountContext={},
        trigger="agent_follow_up",
        signalsToInspect=["volume"],
    )

    response = main._run_automation_analysis(body, "trace-1")

    assert response["success"] is True
    assert response["outcome"] == "wait_and_run_again"
    assert "report" in response["report"].lower()
