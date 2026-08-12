from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from agno.db.in_memory import InMemoryDb
from agno.run.agent import RunOutput

from news_agent.models import NewsAnalysisReport
from news_agent.pipeline import run_news_pipeline


class FakeAgent:
    def __init__(self, response: RunOutput) -> None:
        self.response = response
        self.model = SimpleNamespace(id="poolside/laguna-xs-2.1:free")
        self.calls: list[dict] = []

    def run(self, prompt: str, **kwargs) -> RunOutput:
        self.calls.append({"prompt": prompt, **kwargs})
        return self.response


def test_pipeline_runs_one_persisted_agent(monkeypatch) -> None:
    report = NewsAnalysisReport(
        query="BTC news",
        analyzed_at=datetime.now(UTC),
        executive_summary="Evidence remains limited.",
        aggregate_btc_direction="uncertain",
        aggregate_volatility_risk="uncertain",
    )
    report_response = RunOutput(session_id="btc-thread", content=report)
    analyst = FakeAgent(report_response)

    monkeypatch.setattr("news_agent.pipeline.create_news_agent", lambda **_: analyst)

    result = run_news_pipeline("BTC news", session_id="btc-thread", user_id="alice", db=InMemoryDb())

    assert result.report is report
    assert result.research_tools == []
    assert analyst.calls[0]["session_id"] == "btc-thread"
    assert analyst.calls[0]["user_id"] == "alice"
    assert analyst.calls[0]["prompt"] == "BTC news"
