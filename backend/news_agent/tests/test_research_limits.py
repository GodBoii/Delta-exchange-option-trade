import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from news_agent.budget import ResearchBudget
from news_agent.search import WebSearchTools
from news_agent.tools import FetchResult, NewsResearchTools, deduplicate_articles, fetch_public_document

SETTINGS = SimpleNamespace(allowed_domains=())
HTML = (
    b"<article><p>"
    + b"Current independently reported evidence about markets and monetary policy. " * 12
    + b"</p></article>"
)


@pytest.mark.asyncio
async def test_dossier_fetches_concurrently_and_reuses_article_cache(monkeypatch):
    active = maximum = calls = 0

    async def fetch(url, *_, **__):
        nonlocal active, maximum, calls
        active += 1
        calls += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return FetchResult(url, url, "text/html", HTML)

    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    tools = NewsResearchTools(SETTINGS)
    urls = [f"https://example.com/{i}" for i in range(6)]
    result = json.loads(await tools.build_news_dossier(urls + urls))
    assert 1 < maximum <= 4
    assert calls == 6
    assert result["requested"] == 6
    assert json.loads(await tools.read_news_article(urls[0]))["ok"]
    assert calls == 6


@pytest.mark.asyncio
async def test_nested_reads_cannot_bypass_ten_call_budget(monkeypatch):
    async def fetch(url, *_, **__):
        return FetchResult(url, url, "text/html", HTML)

    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    budget = ResearchBudget()
    tools = NewsResearchTools(SETTINGS, budget)
    await tools.build_news_dossier([f"https://example.com/{i}" for i in range(25)])
    assert budget.calls["read_news_article"] == 10
    result = json.loads(await tools.read_news_article("https://example.com/11"))
    assert not result["ok"]
    assert "10-call limit" in result["error"]
    assert budget.calls["build_news_dossier"] == 1


@pytest.mark.asyncio
async def test_search_limit_is_per_tool_and_cached(monkeypatch):
    from agno.tools.websearch import WebSearchTools as NativeSearch

    calls = []

    def search(*_, **__):
        calls.append(1)
        return json.dumps([{"url": "https://example.com/story", "title": "Story"}])

    monkeypatch.setattr(NativeSearch, "search_news", search)
    monkeypatch.setattr(NativeSearch, "web_search", search)
    tools = WebSearchTools()
    for _ in range(10):
        assert isinstance(json.loads(await tools.search_news("same query")), list)
    assert len(calls) == 1
    assert json.loads(await tools.search_news("eleventh"))["ok"] is False
    assert isinstance(json.loads(await tools.web_search("official evidence")), list)
    assert len(calls) == 2
    fresh = WebSearchTools()
    assert isinstance(json.loads(await fresh.search_news("new run")), list)


@pytest.mark.asyncio
async def test_slow_article_is_cancelled_and_fast_evidence_survives(monkeypatch):
    cancelled = asyncio.Event()

    async def fetch(url, *_, **__):
        if url.endswith("slow"):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        return FetchResult(url, url, "text/html", HTML)

    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    budget = ResearchBudget()
    monkeypatch.setattr(budget, "remaining", lambda maximum: min(maximum, 0.5) if maximum == 30 else maximum)
    tools = NewsResearchTools(SETTINGS, budget)
    result = json.loads(await tools.build_news_dossier(["https://example.com/fast", "https://example.com/slow"]))
    assert result["successful"] == 1
    assert cancelled.is_set()
    assert all(task.done() for task in tools.pending.values())


@pytest.mark.asyncio
async def test_fetch_rejects_private_redirect_and_excessive_body():
    async def redirect(_request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(ValueError, match="Non-public"):
            await fetch_public_document("https://8.8.8.8/article", SETTINGS, client=client)

    async def large(_request):
        return httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(large)) as client:
        with pytest.raises(ValueError, match="4 MiB"):
            await fetch_public_document("https://8.8.8.8/article", SETTINGS, client=client)


def test_copied_bodies_are_collapsed_but_independent_reporting_remains():
    original = " ".join(f"word{i}" for i in range(100))
    independent = " ".join(f"other{i}" for i in range(100))
    items = [
        {"ok": True, "article": {"canonical_url": url, "text": text, "title": "Same event"}}
        for url, text in [("https://a.com", original), ("https://b.com", original), ("https://c.com", independent)]
    ]
    result = deduplicate_articles(items)
    assert len(result) == 2
    assert result[0]["article"]["duplicate_sources"] == ["https://b.com"]
    assert result[1]["article"]["canonical_url"] == "https://c.com"
