from __future__ import annotations

import json

import pytest

from news_agent.budget import ResearchBudget
from news_agent.sources import PublicSourceTools, Source, parse_calendar, parse_feed, parse_polymarket
from news_agent.tools import FetchResult


def test_rss_cards_keep_article_links_and_dates() -> None:
    feed = (
        b"<rss><channel><item><title>Fed meeting statement</title>"
        b"<link>https://www.federalreserve.gov/newsevents/pressreleases/a.htm</link>"
        b"<pubDate>Mon, 28 Sep 2026 12:00:00 GMT</pubDate>"
        b"<description>Rates were held.</description></item></channel></rss>"
    )
    source = Source("Fed", "official", "https://www.federalreserve.gov/feeds/press_all.xml")
    cards = parse_feed(feed, source, "2026-09-28T12:01:00+00:00")
    assert cards[0]["title"] == "Fed meeting statement"
    assert cards[0]["published_at"] == "2026-09-28T12:00:00+00:00"
    assert cards[0]["url"].endswith("/a.htm")


def test_calendar_and_prediction_markets_remain_distinct_from_news() -> None:
    calendar = Source("Forex Factory", "calendar", "https://example.com/week.json")
    cards = parse_calendar(
        b'[{"title":"CPI","country":"USD","date":"2026-09-29T12:30:00Z","forecast":"3.1%"}]',
        calendar,
        "2026-09-28T12:00:00+00:00",
    )
    assert cards[0]["kind"] == "calendar"
    assert "forecast: 3.1%" in cards[0]["excerpt"]
    market = Source("Polymarket", "expectations", "https://gamma-api.polymarket.com/public-search?q=bitcoin")
    cards = parse_polymarket(
        b'{"events":[{"title":"Will rates rise?","slug":"rates-rise","markets":[]}]}',
        market,
        "2026-09-28T12:00:00+00:00",
    )
    assert "not proof" in cards[0]["note"]


def test_bls_calendar_converts_local_ics_times_to_utc() -> None:
    source = Source("BLS calendar", "calendar", "https://www.bls.gov/schedule/news_release/bls.ics")
    event = (
        b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Employment Situation\r\n"
        b"DTSTART;TZID=US-Eastern:20260929T083000\r\nEND:VEVENT\r\nEND:VCALENDAR"
    )
    cards = parse_calendar(event, source, "2026-09-28T12:00:00+00:00")
    assert cards[0]["published_at"] == "2026-09-29T12:30:00+00:00"


@pytest.mark.asyncio
async def test_source_curation_keeps_good_items_when_another_feed_fails(monkeypatch) -> None:
    from news_agent import sources

    good = Source("good", "official", "https://example.com/feed.xml")
    bad = Source("bad", "official", "https://example.org/feed.xml")
    monkeypatch.setattr(sources, "_sources", lambda *_: [good, bad])

    async def fetch(url, *_args, **_kwargs):
        if "example.org" in url:
            raise ValueError("blocked feed")
        return FetchResult(
            url,
            url,
            "application/rss+xml",
            (
                b"<rss><channel><item><title>New policy announcement</title>"
                b"<link>https://example.com/story</link></item></channel></rss>"
            ),
        )

    monkeypatch.setattr(sources, "fetch_public_document", fetch)
    tools = PublicSourceTools(object(), ResearchBudget())
    result = json.loads(await tools.curate_public_sources("ETH", ["policy"]))
    assert len(result["sources"]) == 1
    assert result["errors"][0]["source"] == "bad"


@pytest.mark.asyncio
async def test_public_discussion_search_labels_snippets(monkeypatch) -> None:
    tools = PublicSourceTools(object(), ResearchBudget())
    queries = []

    async def search(query: str, max_results: int = 5) -> str:
        queries.append(query)
        return json.dumps(
            [
                {
                    "title": "Forum view",
                    "url": "https://www.reddit.com/r/ethereum/comments/example",
                    "body": "Some users expect a network update",
                    "date": "2026-09-28",
                }
            ]
        )

    monkeypatch.setattr(tools.search, "web_search", search)
    result = json.loads(await tools.search_public_discussion("Ethereum update", ["reddit"]))
    assert queries == ["site:reddit.com Ethereum update"]
    assert result["posts"][0]["access"] == "search_snippet"
    assert result["posts"][0]["kind"] == "discussion"
