import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from news_agent.agent import _create_research_tools
from news_agent.budget import ResearchBudget
from news_agent.coverage import collection_coverage, coverage_notice
from news_agent.report_quality import clean_report
from news_agent.sources import _sources


def test_topic_query_is_bounded_and_preserves_baseline_sources():
    baseline = _sources("ETH", [])
    expanded = _sources("ETH", ["staking", "staking", "network upgrade", 'site:bad.example OR "oops"'])
    assert {x.url for x in baseline} <= {x.url for x in expanded}
    query = parse_qs(urlsplit(next(x.url for x in expanded if x.name == "Google News topics")).query)["q"][0]
    assert '"staking"' in query and '"network upgrade"' in query
    assert query.count('"staking"') == 1
    assert "site:" not in query
    assert not any(x.name == "Bitcoin Core" for x in expanded)
    assert {"Bank of England news", "Coinbase status", "Kraken status", "Circle status"} <= {
        x.name for x in baseline
    }


def test_toolkits_share_search_instance_and_cache():
    tools = _create_research_tools(SimpleNamespace(allowed_domains=()), ResearchBudget())
    assert tools[0].search is tools[1]
    assert tools[2].source_index is tools[0].source_index


def test_discovery_leaves_time_for_original_verification(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("news_agent.budget.time.monotonic", lambda: clock[0])
    budget = ResearchBudget(seconds=120, limits={"web_search": 20})
    assert budget.snapshot()["remaining_seconds"] == 120
    assert budget.deadline is None
    budget.consume("web_search")
    clock[0] = 180
    with pytest.raises(ValueError, match="Discovery budget finished"):
        budget.consume("search_news")
    budget.consume("read_news_article")
    assert budget.calls["search_news"] == 0
    assert budget.snapshot()["remaining_calls"]["web_search"] == 19
    clock[0] = 221
    with pytest.raises(TimeoutError):
        budget.consume("read_news_article")


def test_coverage_preserves_failure_types_and_latest_observation():
    def call(rows):
        return {"tool_name": "curate_public_sources", "result": json.dumps({"source_health": rows})}

    coverage = collection_coverage([
        call([{"source": "Fed", "status": "timeout"}]),
        call([{"source": "Fed", "status": "available"}, {"source": "Other", "status": "stale"}]),
    ])
    assert coverage["checked_sources"] == 2
    assert coverage["available_sources"] == 1
    assert coverage["limited_sources"] == [{"source": "Other", "status": "stale"}]
    assert "Missing coverage cannot establish" in coverage_notice(coverage)
    assert collection_coverage([])["status"] == "not_collected"
    assert not coverage_notice(collection_coverage([]))


def test_uncited_numbers_do_not_survive_summary_filter():
    result = clean_report(
        "## Summary\n\nInflows reached $900 million and rose 12%.\n\n"
        "## Interpretation\n\nA confirmed increase could support demand.", []
    )
    assert "$900" not in result.markdown
    assert "could support demand" in result.markdown
    assert result.omitted_blocks == 1


@pytest.mark.asyncio
async def test_targeted_read_finds_later_passage_without_download_or_cache_mutation(monkeypatch):
    from news_agent.tools import FetchResult, NewsResearchTools

    calls = []
    text = "Initial background. " * 700 + "Unique evidence about the announced decision. " * 50

    async def fetch(url, *args, **kwargs):
        calls.append(url)
        return FetchResult(url, url, "text/html", f"<article><p>{text}</p></article>".encode())

    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    tools = NewsResearchTools(SimpleNamespace(allowed_domains=()))
    url = "https://example.com/long"
    initial = json.loads(await tools.read_news_article(url))
    assert "Unique evidence" not in initial["article"]["text"]
    later = json.loads(await tools.read_news_article(url, find="Unique evidence"))
    assert later["ok"] and "Unique evidence" in later["article"]["text"]
    assert later["article"]["text_offset"] > 8000
    assert json.loads(await tools.read_news_article(url)) == initial
    assert len(calls) == 1
    assert not json.loads(await tools.read_news_article(url, find="invented phrase"))["ok"]


def test_market_capture_starts_after_news_finishes(monkeypatch):
    from automation_agent import team
    from automation_agent.assets import PROFILES

    completed = []

    def news(*args, **kwargs):
        completed.append("news")
        return SimpleNamespace(markdown="report")

    class Market:
        def __init__(self, **kwargs):
            pass

        def collect_delta_option_context(self):
            assert completed == ["news"]
            return {}

        def collect_market_packet(self):
            assert completed == ["news"]
            return {}

    class ReachedCharts(Exception):
        pass

    def charts(*args):
        raise ReachedCharts

    monkeypatch.setattr(team, "read_parent_run_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(team, "run_news_pipeline", news)
    monkeypatch.setattr(team, "MarketIntelligenceTools", Market)
    monkeypatch.setattr(team, "_chart_artifacts", charts)
    with pytest.raises(ReachedCharts):
        team.run_automation_team(
            settings=SimpleNamespace(), user_id="global", agent_run_id="test", session_id="test",
            account_context={}, trigger="manual", asset=PROFILES["BTC"],
        )
