from __future__ import annotations

from agno.db.in_memory import InMemoryDb
from agno.session.agent import AgentSession

from news_agent import create_news_agent, create_source_research_agent
from news_agent.config import NewsAgentSettings
from news_agent.models import NewsAnalysisReport


def test_agent_is_isolated_and_uses_requested_openrouter_model() -> None:
    db = InMemoryDb()
    agent = create_news_agent(require_api_key=False, db=db)

    assert agent.model.id == "poolside/laguna-xs-2.1:free"
    assert agent.structured_outputs is False
    assert agent.output_schema is NewsAnalysisReport
    assert agent.db is db
    assert agent.add_history_to_context is True
    assert agent.num_history_runs == 3
    assert agent.cache_session is True
    assert [type(toolkit).__name__ for toolkit in agent.tools] == ["WebSearchTools", "NewsResearchTools"]

    tool_names = {name for toolkit in agent.tools for name in toolkit.functions}
    assert tool_names == {
        "build_news_dossier",
        "extract_news_images",
        "inspect_news_source",
        "read_news_article",
        "search_news",
        "search_news_images",
        "web_search",
    }
    assert not any(token in name for name in tool_names for token in ("trade", "order", "delta", "binance"))


def test_source_researcher_has_tools_without_structured_schema() -> None:
    db = InMemoryDb()
    agent = create_source_research_agent(require_api_key=False, db=db)

    assert agent.model.id == "poolside/laguna-xs-2.1:free"
    assert agent.output_schema is None
    assert agent.db is not None
    assert agent.add_history_to_context is True
    assert {name for toolkit in agent.tools for name in toolkit.functions} == {
        "build_news_dossier",
        "extract_news_images",
        "inspect_news_source",
        "read_news_article",
        "search_news",
        "search_news_images",
        "web_search",
    }


def test_agent_uses_injected_database_for_test_sessions() -> None:
    db = InMemoryDb()
    agent = create_news_agent(settings=NewsAgentSettings.load(), require_api_key=False, db=db)
    stored = agent.db.upsert_session(
        AgentSession(
            session_id="btc-test-session",
            agent_id=agent.id,
            user_id="test-user",
            metadata={"purpose": "persistence-test"},
            runs=[],
        )
    )

    reloaded = db.get_session("btc-test-session", user_id="test-user")

    assert stored is not None
    assert reloaded is not None
    assert reloaded.session_id == "btc-test-session"
    assert reloaded.metadata == {"purpose": "persistence-test"}
