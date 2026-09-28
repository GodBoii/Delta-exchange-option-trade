from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from agno.db.in_memory import InMemoryDb
from agno.models.openrouter import OpenRouter
from agno.models.response import ModelResponse
from agno.run.agent import RunOutput
from agno.run.base import RunStatus

from news_agent.pipeline import research_trace, run_news_pipeline
from news_agent.sources import PublicSourceTools


class FakeAgent:
    def __init__(self, responses: list[RunOutput]) -> None:
        self.responses = responses
        self.model = SimpleNamespace(id="deepseek/deepseek-v4.1-flash")
        self.calls: list[dict] = []

    async def arun(self, prompt: str, **kwargs) -> RunOutput:
        self.calls.append({"prompt": prompt, **kwargs})
        return self.responses[len(self.calls) - 1]


def test_pipeline_lets_agent_research_and_returns_trace(monkeypatch) -> None:
    analyst = FakeAgent(
        [
            RunOutput(
                session_id="btc-thread",
                status=RunStatus.completed,
                content="# BTC analysis\n\nEvidence is mixed.",
                tools=[{"tool_name": "search_news", "result": '[{"title":"BTC","url":"https://example.com/btc"}]'}],
            )
        ]
    )
    monkeypatch.setattr("news_agent.pipeline.create_news_agent", lambda **_: analyst)

    result = run_news_pipeline("BTC news", session_id="btc-thread", user_id="alice", db=InMemoryDb())

    assert result.markdown == "# BTC analysis\n\nEvidence is mixed."
    assert result.research_tools == ["search_news"]
    assert result.research_trace[0]["result"]["items"][0]["url"] == "https://example.com/btc"
    assert analyst.calls[0]["session_id"] == "btc-thread"
    assert analyst.calls[0]["user_id"] == "alice"
    assert "search the web yourself" in analyst.calls[0]["prompt"]


@pytest.mark.parametrize("content", ["", "The operation was aborted"])
def test_pipeline_rejects_missing_model_report_without_repeating_inference(monkeypatch, content: str) -> None:
    analyst = FakeAgent([RunOutput(content=content)])
    monkeypatch.setattr("news_agent.pipeline.create_news_agent", lambda **_: analyst)
    with pytest.raises(RuntimeError, match="no report"):
        run_news_pipeline("BTC news", session_id="btc-thread", user_id="alice", db=InMemoryDb())
    assert len(analyst.calls) == 1


def test_research_trace_omits_article_body() -> None:
    body = "research excerpt " * 30
    response = RunOutput(
        tools=[
            {
                "tool_name": "read_news_article",
                "tool_args": {"url": "https://example.com/story"},
                "result": json.dumps(
                    {
                        "ok": True,
                        "article": {
                            "final_url": "https://example.com/story",
                            "title": "Story",
                            "text": body,
                        },
                    }
                ),
            }
        ]
    )
    trace = research_trace(response)
    assert trace[0]["result"]["text_chars"] == len(body)
    assert trace[0]["result"]["excerpt"] == body[:200]
    assert body not in str(trace)


def test_pipeline_rejects_provider_error_content(monkeypatch) -> None:
    analyst = FakeAgent([RunOutput(content="Insufficient credits", status=RunStatus.error)])
    monkeypatch.setattr("news_agent.pipeline.create_news_agent", lambda **_: analyst)
    with pytest.raises(RuntimeError, match="no report"):
        run_news_pipeline("ETH news", db=InMemoryDb(), asset="ETH")


def test_pipeline_rejects_tool_syntax_masquerading_as_report(monkeypatch) -> None:
    analyst = FakeAgent(
        [RunOutput(content="<｜DSML｜ calls>curate_public_sources</｜DSML｜ calls>", status=RunStatus.completed)]
    )
    monkeypatch.setattr("news_agent.pipeline.create_news_agent", lambda **_: analyst)
    with pytest.raises(RuntimeError, match="no report"):
        run_news_pipeline("ETH news", db=InMemoryDb(), asset="ETH")


def test_real_agno_async_dispatch_executes_research_tool(monkeypatch) -> None:
    """Exercise Agno's tool registration and execution, without an API request."""
    model_requests: list[list[str]] = []

    async def curate(self, asset: str, topics: list[str], lookback_hours: int = 48) -> str:
        return json.dumps(
            {
                "asset": asset,
                "sources": [{"title": "Fed meeting", "url": "https://www.federalreserve.gov/"}],
                "errors": [],
            }
        )

    curate.__name__ = "curate_public_sources"

    async def invoke(self, *args, **kwargs) -> ModelResponse:
        model_requests.append([tool["function"]["name"] for tool in kwargs["tools"]])
        if len(model_requests) == 1:
            return ModelResponse(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_curate",
                        "type": "function",
                        "function": {
                            "name": "curate_public_sources",
                            "arguments": json.dumps({"asset": "ETH", "topics": ["rates"]}),
                        },
                    }
                ],
            )
        return ModelResponse(role="assistant", content="## Summary\n\n[Fed](https://www.federalreserve.gov/)")

    monkeypatch.setattr(PublicSourceTools, "curate_public_sources", curate)
    monkeypatch.setattr(OpenRouter, "ainvoke", invoke)
    result = run_news_pipeline("ETH news", db=InMemoryDb(), asset="ETH", debug_mode=False)

    assert "curate_public_sources" in model_requests[0]
    assert len(model_requests) == 2
    assert result.research_tools == ["curate_public_sources"]
    assert result.research_trace[0]["result"]["count"] == 1
