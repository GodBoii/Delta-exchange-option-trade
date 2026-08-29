from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.session.agent import AgentSession

from news_agent.config import NewsAgentSettings
from news_agent.database import create_session_db
from news_analyzer import main


def report(direction: str = "mixed") -> str:
    return f"# BTC analysis\n\nThe evidence currently points to a **{direction}** outlook."


def test_supabase_database_factory_uses_agno_postgres(monkeypatch) -> None:
    settings = replace(
        NewsAgentSettings.load(),
        supabase_db_url="postgresql://postgres:secret@example.supabase.co:5432/postgres",
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
    assert parse_qs(parsed_url.query)["sslmode"] == ["require"]
    assert "connect_timeout" not in parse_qs(parsed_url.query)
def test_saved_supabase_session_shapes_current_and_history() -> None:
    session_id = "news:user-9:btc-news-desk"
    session = AgentSession(
        session_id=session_id,
        agent_id="news-intelligence-analyst",
        user_id="user-9",
        runs=[
            RunOutput(
                run_id="previous-run",
                model="model-a",
                content=report("bearish"),
                status=RunStatus.completed,
            ),
            RunOutput(
                run_id="current-run",
                model="model-a",
                content=report("bullish"),
                status=RunStatus.completed,
            ),
        ],
    )
    session.runs[-1].tools = [ToolExecution(tool_name="search_news", result="[]")]

    response = main._response_payload(
        session=session,
        public_session_id="btc-news-desk",
    )

    assert response["runId"] == "current-run"
    assert "**bullish**" in response["analysis"]
    assert response["history"][0]["runId"] == "previous-run"
    assert response["researchTools"] == ["search_news"]


def test_session_list_is_user_scoped_and_returns_saved_session_summaries(monkeypatch) -> None:
    owned_session = AgentSession(
        session_id="news:user-9:btc-news-123",
        agent_id="news-intelligence-analyst",
        user_id="user-9",
        runs=[RunOutput(run_id="owned-run", model="model-a", content=report("bullish"))],
        created_at=1_786_533_339,
        updated_at=1_786_533_400,
    )
    unrelated_session = AgentSession(
        session_id="news:other-user:btc-news-456",
        agent_id="news-intelligence-analyst",
        user_id="other-user",
        runs=[RunOutput(run_id="other-run", model="model-a", content=report("bearish"))],
    )
    captured: dict = {}

    class FakeDb:
        def get_sessions(self, **kwargs):
            captured.update(kwargs)
            return [owned_session, unrelated_session]

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(main, "_open_session_db", lambda: FakeDb())

    response = main._list_sessions("user-9")

    assert captured["user_id"] == "user-9"
    assert captured["component_id"] == "news-intelligence-analyst"
    assert captured["limit"] is None
    assert captured["closed"] is True
    assert response["sessions"] == [
        {
            "sessionId": "btc-news-123",
            "runId": "owned-run",
            "model": "model-a",
            "createdAt": 1_786_533_339,
            "updatedAt": 1_786_533_400,
            "runCount": 1,
            "preview": "BTC analysis The evidence currently points to a bullish outlook.",
        }
    ]


@pytest.mark.asyncio
async def test_health_reports_supabase_postgres(monkeypatch) -> None:
    monkeypatch.setattr(main, "settings", replace(main.settings, supabase_db_url="postgresql+psycopg://configured"))
    monkeypatch.setattr(main, "_database_status", lambda: (True, None))
    response = await main.health()
    assert response["service"] == "news-analyzer"
    assert response["database"] == "supabase-postgres"
    assert response["databaseConfigured"] is True
    assert response["databaseReady"] is True
    assert response["sessionTable"] == main.settings.session_table


def test_database_failure_is_not_misreported_as_missing_session(monkeypatch) -> None:
    fake_db = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(main, "create_session_db", lambda _: fake_db)
    monkeypatch.setattr(main, "verify_session_db", lambda _: (_ for _ in ()).throw(OSError("connection failed")))

    with pytest.raises(main.ServiceError) as caught:
        main._load_response("user-9", "btc-news-desk")

    assert caught.value.status == 503
    assert caught.value.code == "news_database_unavailable"


def test_analysis_reads_the_persisted_run_from_the_same_database(monkeypatch) -> None:
    saved_report = report("bullish")
    session = AgentSession(
        session_id="news:user-9:btc-news-desk",
        agent_id="news-intelligence-analyst",
        user_id="user-9",
        runs=[RunOutput(run_id="saved-run", model="model-a", content=saved_report)],
    )
    fake_db = SimpleNamespace(
        close=lambda: None,
        get_session=lambda session_id, user_id: session,
    )
    captured: dict = {}

    def fake_pipeline(prompt, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return SimpleNamespace(markdown=saved_report, research_tools=["search_news"])

    monkeypatch.setattr(main, "_open_session_db", lambda: fake_db)
    monkeypatch.setattr(main, "run_news_pipeline", fake_pipeline)

    response = main._run_analysis(
        main.NewsAnalysisRequest(query="Latest BTC macro news", sessionId="btc-news-desk", userId="user-9")
    )

    assert captured["db"] is fake_db
    assert captured["session_id"] == "news:user-9:btc-news-desk"
    assert response["runId"] == "saved-run"
    assert "**bullish**" in response["analysis"]


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
