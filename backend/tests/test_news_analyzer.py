from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.session.agent import AgentSession

from news_agent.config import NewsAgentSettings
from news_agent.database import create_session_db
from news_agent.models import NewsAnalysisReport
from news_analyzer import main


def report(direction: str = "mixed") -> NewsAnalysisReport:
    return NewsAnalysisReport(
        query="Latest BTC macro news",
        analyzed_at=datetime.now(UTC),
        executive_summary="Evidence suggests mixed direction and elevated volatility.",
        aggregate_btc_direction=direction,
        aggregate_volatility_risk="high",
    )


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
    assert parse_qs(parsed_url.query)["connect_timeout"] == ["10"]
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
                content=report("bearish").model_dump_json(),
                status=RunStatus.completed,
            ),
            RunOutput(
                run_id="current-run",
                model="model-a",
                content=report("bullish").model_dump(mode="json"),
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
    assert response["report"]["aggregate_btc_direction"] == "bullish"
    assert response["history"][0]["runId"] == "previous-run"
    assert response["researchTools"] == ["search_news"]


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
        runs=[RunOutput(run_id="saved-run", model="model-a", content=saved_report.model_dump(mode="json"))],
    )
    fake_db = SimpleNamespace(
        close=lambda: None,
        get_session=lambda session_id, user_id: session,
    )
    captured: dict = {}

    def fake_pipeline(prompt, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return SimpleNamespace(report=saved_report)

    monkeypatch.setattr(main, "_open_session_db", lambda: fake_db)
    monkeypatch.setattr(main, "run_news_pipeline", fake_pipeline)

    response = main._run_analysis(
        main.NewsAnalysisRequest(query="Latest BTC macro news", sessionId="btc-news-desk", userId="user-9")
    )

    assert captured["db"] is fake_db
    assert captured["session_id"] == "news:user-9:btc-news-desk"
    assert response["runId"] == "saved-run"
    assert response["report"]["aggregate_btc_direction"] == "bullish"
