from __future__ import annotations

from types import SimpleNamespace

from agno.db.in_memory import InMemoryDb
from agno.run.agent import RunOutput

from news_agent.pipeline import run_news_pipeline


class FakeAgent:
    def __init__(self, responses: list[RunOutput]) -> None:
        self.responses = responses
        self.model = SimpleNamespace(id="z-ai/glm-5.3-flash")
        self.calls: list[dict] = []

    def run(self, prompt: str, **kwargs) -> RunOutput:
        self.calls.append({"prompt": prompt, **kwargs})
        return self.responses[len(self.calls) - 1]


def _research_context(*_) -> tuple[str, tuple[str, ...]]:
    return '[{"title":"BTC evidence","url":"https://example.com/btc"}]', ("search_news",)


def test_pipeline_returns_native_markdown(monkeypatch) -> None:
    analyst = FakeAgent([RunOutput(session_id="btc-thread", content="# BTC analysis\n\nEvidence is mixed.")])
    monkeypatch.setattr("news_agent.pipeline.create_news_agent", lambda **_: analyst)
    monkeypatch.setattr("news_agent.pipeline._collect_live_news_context", _research_context)

    result = run_news_pipeline("BTC news", session_id="btc-thread", user_id="alice", db=InMemoryDb())

    assert result.markdown == "# BTC analysis\n\nEvidence is mixed."
    assert result.research_tools == ["search_news"]
    assert analyst.calls[0]["session_id"] == "btc-thread"
    assert analyst.calls[0]["user_id"] == "alice"
    assert "Respond naturally in Markdown" in analyst.calls[0]["prompt"]
    assert "https://example.com/btc" in analyst.calls[0]["prompt"]


def test_pipeline_retries_empty_synthesis_once(monkeypatch) -> None:
    analyst = FakeAgent([RunOutput(content=""), RunOutput(content="## Completed\n\nUncertainty remains.")])
    monkeypatch.setattr("news_agent.pipeline.create_news_agent", lambda **_: analyst)
    monkeypatch.setattr("news_agent.pipeline._collect_live_news_context", _research_context)

    result = run_news_pipeline("BTC news", session_id="btc-thread", user_id="alice", db=InMemoryDb())

    assert result.markdown == "## Completed\n\nUncertainty remains."
    assert len(analyst.calls) == 2
    assert "previous response was empty" in analyst.calls[1]["prompt"]
