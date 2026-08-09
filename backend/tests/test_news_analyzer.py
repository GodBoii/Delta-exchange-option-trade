from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

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
        session_db_schema="ai",
        session_table="news_sessions",
    )
    captured: dict = {}
    fake_db = SimpleNamespace()

    def fake_postgres(**kwargs):
        captured.update(kwargs)
        return fake_db

    monkeypatch.setattr("news_agent.database.PostgresDb", fake_postgres)

    assert create_session_db(settings) is fake_db
    assert captured == {
        "db_url": "postgresql+psycopg://postgres:secret@example.supabase.co:5432/postgres",
        "db_schema": "ai",
        "session_table": "news_sessions",
        "create_schema": True,
    }


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
    research = AgentSession(
        session_id=f"{session_id}:research",
        agent_id="news-intelligence-source-researcher",
        user_id="user-9",
        runs=[RunOutput(tools=[ToolExecution(tool_name="search_news", result="[]")])],
    )

    response = main._response_payload(
        session=session,
        research_session=research,
        public_session_id="btc-news-desk",
    )

    assert response["runId"] == "current-run"
    assert response["report"]["aggregate_btc_direction"] == "bullish"
    assert response["history"][0]["runId"] == "previous-run"
    assert response["researchTools"] == ["search_news"]


@pytest.mark.asyncio
async def test_health_reports_supabase_postgres(monkeypatch) -> None:
    monkeypatch.setattr(main, "settings", replace(main.settings, supabase_db_url="postgresql+psycopg://configured"))
    response = await main.health()
    assert response["service"] == "news-analyzer"
    assert response["database"] == "supabase-postgres"
    assert response["databaseConfigured"] is True
