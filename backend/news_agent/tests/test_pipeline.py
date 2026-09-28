from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from agno.db.in_memory import InMemoryDb
from agno.run.agent import RunOutput
from agno.run.base import RunStatus

from news_agent.pipeline import research_trace, run_news_pipeline


class FakeAgent:
    def __init__(self, responses: list[RunOutput]) -> None:
        self.responses = responses
        self.model = SimpleNamespace(id="deepseek/deepseek-v4.1-flash")
        self.calls: list[dict] = []

    def run(self, prompt: str, **kwargs) -> RunOutput:
        self.calls.append({"prompt": prompt, **kwargs})
        return self.responses[len(self.calls) - 1]


def test_pipeline_lets_agent_research_and_returns_trace(monkeypatch) -> None:
    analyst = FakeAgent(
        [
            RunOutput(
                session_id="btc-thread",
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
