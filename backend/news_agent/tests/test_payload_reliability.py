import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from news_agent.budget import ResearchBudget
from news_agent.search import WebSearchTools
from news_agent.sources import Source, _sources, parse_listing, select_source_cards
from news_agent.tools import FetchResult, NewsResearchTools


@pytest.fixture(autouse=True)
def forbid_external_http(monkeypatch):
    client = httpx.AsyncClient

    def unexpected(request):
        raise AssertionError(f"Unmocked HTTP request: {request.url}")

    def isolated_client(**kwargs):
        return client(**kwargs, transport=httpx.MockTransport(unexpected))

    monkeypatch.setattr(httpx, "AsyncClient", isolated_client)


@pytest.mark.asyncio
@pytest.mark.parametrize("iteration", range(5))
async def test_64_identical_searches_share_one_upstream_request(monkeypatch, iteration):
    from agno.tools.websearch import WebSearchTools as NativeSearch

    calls = []

    def search(*args, **kwargs):
        calls.append(1)
        time.sleep(0.03)
        return json.dumps([{"url": "https://example.com/news", "title": "Current release"}])

    monkeypatch.setattr(NativeSearch, "web_search", search)
    tool = WebSearchTools()
    results = await asyncio.gather(*(tool.web_search("same official release") for _ in range(64)))
    assert len(set(results)) == 1 and isinstance(json.loads(results[0]), list)
    assert len(calls) == tool.budget.calls["web_search"] == 1


@pytest.mark.asyncio
async def test_search_payload_labels_old_future_and_undated_material(monkeypatch):
    from agno.tools.websearch import WebSearchTools as NativeSearch

    now = datetime.now(UTC)
    rows = [
        {"url": "https://example.com/old", "date": (now - timedelta(days=10)).isoformat()},
        {"url": "https://example.com/future", "date": (now + timedelta(days=1)).isoformat()},
        {"url": "https://example.com/unknown", "date": "5 hours ago"},
    ]
    monkeypatch.setattr(NativeSearch, "web_search", lambda *args, **kwargs: json.dumps(rows))
    result = json.loads(await WebSearchTools().web_search("dates"))
    assert [row["publication_status"] for row in result] == ["undated", "stale", "future_date"]
    assert all(row["retrieved_at"] and row["access"] == "discovery_snippet" for row in result)
    assert result[0]["date"] is None and result[0]["reported_date"] == "5 hours ago"


def test_feed_selection_sorts_recent_items_before_limiting():
    now = datetime.now(UTC)
    rows = [
        {"url": f"https://example.com/{i}", "published_at": (now - timedelta(hours=10 - i)).isoformat()}
        for i in range(8)
    ]
    selected = select_source_cards(rows, Source("Feed", "publisher", "https://example.com/rss"), now, 48)
    assert selected[0]["url"] == "https://example.com/7"
    assert selected[-1]["url"] == "https://example.com/4"


@pytest.mark.asyncio
@pytest.mark.parametrize("iteration", range(5))
async def test_32_article_readers_share_one_download_and_receive_clean_json(monkeypatch, iteration):
    calls = []
    text = "A public release with numbers 3.1% and Unicode 日本語. " * 20

    async def fetch(url, *args, **kwargs):
        calls.append(url)
        await asyncio.sleep(0.01)
        return FetchResult(url, url, "text/html", f"<article><p>{text}</p></article>".encode())

    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    tool = NewsResearchTools(SimpleNamespace(allowed_domains=()), ResearchBudget())
    results = await asyncio.gather(*(tool.read_news_article("https://example.com/release") for _ in range(32)))
    assert len(calls) == tool.budget.calls["read_news_article"] == 1
    assert len(set(results)) == 1
    article = json.loads(results[0])["article"]
    assert "日本語" in article["text"] and "<p>" not in article["text"]
    assert article["retrieved_at"] and article["publication_status"] == "undated"


def test_official_tax_and_sanctions_listing_dates_are_scoped_to_each_release():
    body = b"""<article>
    <div class="field--item"><div><h2><a rel="bookmark" href="/newsroom/first">
    First tax policy announcement</a></h2></div>
    <p>IR-2026-128, Oct. 23, 2026 - Tax announcement.</p></div>
    <div class="field--item"><div><h2><a rel="bookmark" href="/newsroom/second">
    Second tax policy announcement</a></h2></div>
    <p>IR-2026-127, Oct. 22, 2026 - Other tax announcement.</p></div>
    <a href="/newsroom/archive">Newsroom navigation is not a release</a></article>"""
    rows = parse_listing(body, Source("IRS tax releases", "official_listing", "https://www.irs.gov/newsroom"), "now")
    assert len(rows) == 2
    assert [r["published_at"] for r in rows] == ["2026-10-23T00:00:00+00:00", "2026-10-22T00:00:00+00:00"]
    body = b"""<div class="views-row"><div><a href="/recent-actions/20261021">
    Sanctions announcement for a region</a></div>
    <div>October 21, 2026</div></div><a href="/recent-actions">Navigation should be excluded</a>"""
    rows = parse_listing(
        body, Source("OFAC sanctions", "official_listing", "https://ofac.treasury.gov/recent-actions"), "now"
    )
    assert len(rows) == 1 and rows[0]["published_at"] == "2026-10-21T00:00:00+00:00"


def test_default_discovery_has_explicit_requested_coverage():
    names = {s.name for s in _sources("ETH", [])}
    assert {
        "IRS tax releases",
        "OFAC sanctions",
        "EU sanctions guidance",
        "Crypto law and regulation",
        "Tax and fiscal policy",
        "Country economic reports",
        "Trade tariffs and sanctions",
        "War energy and shipping",
        "Regional crypto policy",
    } <= names


@pytest.mark.asyncio
async def test_search_cancellation_does_not_cancel_another_waiter(monkeypatch):
    from agno.tools.websearch import WebSearchTools as NativeSearch

    def search(*args, **kwargs):
        time.sleep(0.05)
        return json.dumps([{"url": "https://example.com/report"}])

    monkeypatch.setattr(NativeSearch, "web_search", search)
    tool = WebSearchTools()
    first = asyncio.create_task(tool.web_search("shared"))
    second = asyncio.create_task(tool.web_search("shared"))
    await asyncio.sleep(0.01)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert isinstance(json.loads(await second), list)
    assert tool.budget.calls["web_search"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("iteration", range(5))
async def test_64_collectors_share_one_source_fetch_and_cannot_mutate_cached_cards(monkeypatch, iteration):
    from news_agent import sources
    from news_agent.source_cache import SourceCache

    calls = []
    monkeypatch.setattr(sources, "SOURCE_CACHE", SourceCache())
    monkeypatch.setattr(sources, "_sources", lambda *args: [Source("Official", "official", "https://example.com/rss")])

    async def fetch(url, *args, **kwargs):
        calls.append(url)
        await asyncio.sleep(0.01)
        return FetchResult(
            url,
            url,
            "application/rss+xml",
            b"<rss><channel><item><title>Public official release</title><link>https://example.com/a</link></item></channel></rss>",
        )

    monkeypatch.setattr(sources, "fetch_public_document", fetch)
    tools = [sources.PublicSourceTools(SimpleNamespace(allowed_domains=()), ResearchBudget()) for _ in range(64)]
    results = await asyncio.gather(*(t.curate_public_sources("BTC", []) for t in tools))
    assert len(calls) == 1 and all(json.loads(r)["sources"] for r in results)
    first = json.loads(results[0])
    first["sources"][0]["title"] = "mutated"
    assert json.loads(await tools[0].curate_public_sources("BTC", []))["sources"][0]["title"] != "mutated"


@pytest.mark.asyncio
async def test_recent_fallback_leads_are_not_displaced_by_stale_provider_results(monkeypatch):
    from agno.tools.websearch import WebSearchTools as NativeSearch

    old = [{"url": f"https://example.com/old{i}", "date": "2020-01-01T00:00:00Z"} for i in range(5)]
    monkeypatch.setattr(NativeSearch, "search_news", lambda *args, **kwargs: json.dumps(old))

    async def current(*args):
        return [{"url": f"https://example.com/new{i}", "date": datetime.now(UTC).isoformat()} for i in range(5)]

    monkeypatch.setattr(WebSearchTools, "_google_news", current)
    rows = json.loads(await WebSearchTools().search_news("crypto", max_results=5))
    assert len(rows) == 5 and all(row["publication_status"] == "recent" for row in rows)


@pytest.mark.asyncio
async def test_cached_search_expires_and_downloads_a_new_result(monkeypatch):
    from agno.tools.websearch import WebSearchTools as NativeSearch

    calls = []

    def search(*args, **kwargs):
        calls.append(1)
        return json.dumps([{"url": f"https://example.com/{len(calls)}"}])

    monkeypatch.setattr(NativeSearch, "web_search", search)
    tool = WebSearchTools()
    first = await tool.web_search("changing news")
    assert first == await tool.web_search("changing news")
    tool.cache_expires[("web_search", "changing news", 10)] = 0
    assert await tool.web_search("changing news") != first
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_original_character_encoding_is_preserved_in_article_json(monkeypatch):
    text = "Die Zinsen sind höher. Die Veröffentlichung enthält europäische Finanzdaten. " * 10
    html = f'<html><head><meta charset="iso-8859-1"></head><body><article><p>{text}</p></article></body></html>'

    async def fetch(url, *args, **kwargs):
        return FetchResult(url, url, "text/html", html.encode("iso-8859-1"))

    monkeypatch.setattr("news_agent.tools.fetch_public_document", fetch)
    tool = NewsResearchTools(SimpleNamespace(allowed_domains=()))
    article = json.loads(await tool.read_news_article("https://example.com/release"))["article"]
    assert "höher" in article["text"] and "Veröffentlichung" in article["text"]
    assert "\ufffd" not in article["text"]


@pytest.mark.asyncio
async def test_disk_cache_shares_download_between_16_workers_and_repairs_corruption(tmp_path):
    from news_agent.source_cache import FileSourceCache, SourceSnapshot

    calls = []

    async def load():
        calls.append(1)
        await asyncio.sleep(0.01)
        return SourceSnapshot([{"title": "Official release"}], datetime.now(UTC).isoformat())

    results = await asyncio.gather(*(FileSourceCache(tmp_path).get(("feed",), load) for _ in range(16)))
    assert len(calls) == 1 and all(result.cards for result in results)
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text())
    payload["snapshot"]["cards"] = "corrupted"
    path.write_text(json.dumps(payload))
    restored = await FileSourceCache(tmp_path).get(("feed",), load)
    assert restored.cards[0]["title"] == "Official release" and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_slow_source_does_not_discard_completed_fast_sources(monkeypatch, cancelled):
    from news_agent import sources
    from news_agent.source_cache import SourceCache

    monkeypatch.setattr(sources, "SOURCE_CACHE", SourceCache())
    monkeypatch.setattr(
        sources,
        "_sources",
        lambda *args: [
            Source("Fast", "official", "https://example.com/fast"),
            Source("Slow", "official", "https://example.com/slow"),
        ],
    )

    async def fetch(url, *args, **kwargs):
        if url.endswith("slow"):
            if cancelled:
                raise asyncio.CancelledError()
            await asyncio.sleep(5)
        return FetchResult(
            url,
            url,
            "application/rss+xml",
            b"<rss><channel><item><title>Official public release</title><link>https://example.com/a</link></item></channel></rss>",
        )

    monkeypatch.setattr(sources, "fetch_public_document", fetch)
    budget = ResearchBudget()
    monkeypatch.setattr(budget, "remaining", lambda maximum: 0.03 if maximum == 45 else maximum)
    result = json.loads(
        await sources.PublicSourceTools(SimpleNamespace(allowed_domains=()), budget).curate_public_sources("BTC", [])
    )
    assert len(result["sources"]) == 1 and result["sources"][0]["source"] == "Fast"
    assert any(row["source"] == "Slow" and row["code"] == "timeout" for row in result["errors"])
