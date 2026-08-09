from __future__ import annotations

from news_agent import create_news_agent
from news_agent.models import NewsAnalysisReport


def test_agent_is_isolated_and_uses_requested_openrouter_model() -> None:
    agent = create_news_agent(require_api_key=False)

    assert agent.model.id == "xiaomi/mimo-v2.5"
    assert agent.structured_outputs is True
    assert agent.output_schema is NewsAnalysisReport
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
