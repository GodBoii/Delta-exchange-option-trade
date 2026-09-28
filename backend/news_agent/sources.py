"""Public source discovery for the news analyst, without event or trade decisions."""

from __future__ import annotations

import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import httpx
from agno.tools import Toolkit
from bs4 import BeautifulSoup

from .budget import ResearchBudget
from .config import NewsAgentSettings
from .search import WebSearchTools
from .tools import canonicalize_url, fetch_public_document

SOURCE_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
        "application/json",
        "text/json",
        "text/calendar",
        "text/plain",
        "text/html",
        "",
    }
)


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    kind: str
    url: str
    assets: tuple[str, ...] = ("BTC", "ETH")


SOURCES = (
    Source("Federal Reserve monetary policy", "official", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    Source("Federal Reserve releases", "official", "https://www.federalreserve.gov/feeds/press_all.xml"),
    Source("SEC releases", "official", "https://www.sec.gov/rss/news/press.xml"),
    Source("ECB releases", "official", "https://www.ecb.europa.eu/rss/press.html"),
    Source("BLS calendar", "calendar", "https://www.bls.gov/schedule/news_release/bls.ics"),
    Source("Forex Factory calendar", "calendar", "https://nfs.faireconomy.media/ff_calendar_thisweek.json"),
    Source("CoinDesk", "publisher", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    Source("Cointelegraph Bitcoin", "publisher", "https://cointelegraph.com/rss/tag/bitcoin", ("BTC",)),
    Source("Cointelegraph Ethereum", "publisher", "https://cointelegraph.com/rss/tag/ethereum", ("ETH",)),
    Source("Ethereum Foundation", "organization", "https://blog.ethereum.org/en/feed.xml", ("ETH",)),
    Source("Binance announcements", "organization", "https://www.binance.com/en/support/announcement/list/000"),
    Source("Bitcoin Core", "organization", "https://bitcoin.org/en/version-history", ("BTC",)),
)
DISCUSSION_SITES = {
    "reddit": "reddit.com",
    "x": "x.com",
    "threads": "threads.net",
    "facebook": "facebook.com",
    "binance_square": "binance.com/en/square",
}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True).split())[:limit]


def _date(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return datetime.fromisoformat(raw).replace(tzinfo=UTC).isoformat()
    if re.fullmatch(r"\d{8}T\d{6}Z?", raw):
        parsed = datetime.strptime(raw.rstrip("Z"), "%Y%m%dT%H%M%S")
        return parsed.replace(tzinfo=UTC).isoformat() if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return raw[:80]
    return parsed.astimezone(UTC).isoformat() if parsed.tzinfo else raw[:80]


def _card(
    *,
    source: Source,
    title: Any,
    url: Any,
    published: Any = None,
    excerpt: Any = None,
    retrieved: str,
    author: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    resolved = canonicalize_url(urljoin(source.url, str(url or "")))
    if not resolved.startswith(("https://", "http://")) or not _text(title):
        return None
    return {
        "source": source.name,
        "kind": source.kind,
        "title": _text(title, 220),
        "url": resolved,
        "published_at": _date(published),
        "retrieved_at": retrieved,
        "excerpt": _text(excerpt),
        "author": _text(author, 100) or None,
        **(extra or {}),
    }


def parse_feed(body: bytes, source: Source, retrieved: str) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    items = root.findall(".//item") or root.findall("{http://www.w3.org/2005/Atom}entry")
    cards = []
    for item in items[:20]:

        def field(*names: str, current: ET.Element = item) -> str | None:
            for name in names:
                element = current.find(name)
                if element is not None and element.text:
                    return element.text
            return None

        link = item.find("link")
        href = link.get("href") if link is not None and link.get("href") else field("link")
        card = _card(
            source=source,
            title=field("title", "{http://www.w3.org/2005/Atom}title"),
            url=href,
            published=field(
                "pubDate", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"
            ),
            excerpt=field("description", "{http://www.w3.org/2005/Atom}summary"),
            retrieved=retrieved,
        )
        if card:
            cards.append(card)
    return cards


def parse_calendar(body: bytes, source: Source, retrieved: str) -> list[dict[str, Any]]:
    if source.url.endswith(".json"):
        entries = json.loads(body)
        if not isinstance(entries, list):
            raise ValueError("Calendar did not return a list")
        cards = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title")
            detail = ", ".join(
                f"{key}: {entry[key]}"
                for key in ("country", "impact", "forecast", "previous", "actual")
                if entry.get(key) not in (None, "")
            )
            card = _card(
                source=source,
                title=title,
                url=source.url,
                published=entry.get("date"),
                excerpt=detail,
                retrieved=retrieved,
            )
            if card:
                cards.append(card)
        return cards
    lines = re.sub(r"\r?\n[ \t]", "", body.decode("utf-8-sig", errors="replace")).splitlines()
    cards = []
    event: dict[str, str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT" and event is not None:
            starts = event.get("DTSTART")
            if starts and re.fullmatch(r"\d{8}T\d{6}", starts):
                timezone = event.get("_TIMEZONE") or "America/New_York"
                if timezone == "US-Eastern":
                    timezone = "America/New_York"
                with suppress(ValueError, KeyError):
                    starts = (
                        datetime.strptime(starts, "%Y%m%dT%H%M%S")
                        .replace(tzinfo=ZoneInfo(timezone))
                        .astimezone(UTC)
                        .isoformat()
                    )
            card = _card(
                source=source,
                title=event.get("SUMMARY"),
                url=event.get("URL") or source.url,
                published=starts,
                excerpt=event.get("DESCRIPTION"),
                retrieved=retrieved,
            )
            if card:
                cards.append(card)
            event = None
        elif event is not None and ":" in line:
            key, value = line.split(":", 1)
            if "TZID=" in key:
                event["_TIMEZONE"] = key.split("TZID=", 1)[1].split(";", 1)[0]
            event[key.split(";", 1)[0]] = value
    return cards


def parse_listing(body: bytes, source: Source, retrieved: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(body, "lxml")
    cards = []
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        if source.name == "Binance announcements" and "/support/announcement/detail/" not in href:
            continue
        if source.name == "Bitcoin Core" and "/en/releases/" not in href:
            continue
        title = link.get_text(" ", strip=True)
        published = None
        if source.name == "Bitcoin Core" and link.parent:
            heading = link.parent.select_one(".post-name")
            date = link.parent.select_one(".post-date")
            title = heading.get_text(" ", strip=True) if heading else title
            published = date.get_text(" ", strip=True) if date else None
        if len(title) < (10 if source.name == "Bitcoin Core" else 18):
            continue
        card = _card(source=source, title=title, url=href, published=published, retrieved=retrieved)
        if card:
            cards.append(card)
        if len(cards) >= 25:
            break
    return cards


def parse_gdelt(body: bytes, source: Source, retrieved: str) -> list[dict[str, Any]]:
    data = json.loads(body)
    cards = []
    for item in data.get("articles", [])[:30]:
        card = _card(
            source=source,
            title=item.get("title"),
            url=item.get("url"),
            published=item.get("seendate"),
            excerpt=item.get("domain"),
            retrieved=retrieved,
        )
        if card:
            cards.append(card)
    return cards


def parse_polymarket(body: bytes, source: Source, retrieved: str) -> list[dict[str, Any]]:
    data = json.loads(body)
    cards = []
    for item in data.get("events", [])[:12]:
        if not isinstance(item, dict):
            continue
        markets = item.get("markets") or []
        detail = _text(item.get("description"), 300)
        if markets and isinstance(markets[0], dict):
            market = markets[0]
            detail += f" Outcomes: {market.get('outcomes')}; current prices: {market.get('outcomePrices')}."
        card = _card(
            source=source,
            title=item.get("title"),
            url=f"https://polymarket.com/event/{item.get('slug')}",
            published=item.get("startDate"),
            excerpt=detail,
            retrieved=retrieved,
            extra={"note": "Market expectations, not proof that the event occurred"},
        )
        if card:
            cards.append(card)
    return cards


def _sources(asset: str, topics: list[str]) -> list[Source]:
    selected = [source for source in SOURCES if asset in source.assets]
    query = " ".join(topics).strip()[:160]
    for label, term in (
        ("asset", "Ethereum OR ETH" if asset == "ETH" else "Bitcoin OR BTC"),
        ("global", query or "central bank inflation geopolitics"),
    ):
        params = urlencode(
            {
                "query": term,
                "mode": "artlist",
                "format": "json",
                "timespan": "2d",
                "maxrecords": 25,
                "sort": "datedesc",
            }
        )
        selected.append(Source(f"GDELT {label}", "discovery", f"https://api.gdeltproject.org/api/v2/doc/doc?{params}"))
    params = urlencode({"q": "ethereum" if asset == "ETH" else "bitcoin", "limit_per_type": 5})
    selected.append(Source("Polymarket", "expectations", f"https://gamma-api.polymarket.com/public-search?{params}"))
    return selected


class PublicSourceTools(Toolkit):
    def __init__(self, settings: NewsAgentSettings, budget: ResearchBudget, **kwargs: Any) -> None:
        self.settings = settings
        self.budget = budget
        self.search = WebSearchTools(budget)
        super().__init__(
            name="public_source_tools",
            tools=[self.curate_public_sources, self.search_public_discussion],
            **kwargs,
        )

    async def search_public_discussion(self, topic: str, platforms: list[str]) -> str:
        """Discover public discussion snippets on named platforms without claiming post verification."""
        try:
            self.budget.consume("search_public_discussion")
        except (TimeoutError, ValueError) as error:
            return json.dumps({"posts": [], "errors": [{"platform": "search", "error": str(error)}]})
        selected = list(
            dict.fromkeys(platform.lower() for platform in platforms if platform.lower() in DISCUSSION_SITES)
        )[:5]
        if not selected:
            selected = list(DISCUSSION_SITES)
        retrieved = datetime.now(UTC).isoformat()

        async def discover(platform: str) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
            query = f"site:{DISCUSSION_SITES[platform]} {topic[:120]}"
            result = json.loads(await self.search.web_search(query, max_results=5))
            if not isinstance(result, list):
                return [], {"platform": platform, "error": str(result.get("error") or "Search unavailable")}
            posts = []
            for row in result:
                if not isinstance(row, dict) or not row.get("url"):
                    continue
                posts.append(
                    {
                        "platform": platform,
                        "kind": "discussion",
                        "title": row.get("title"),
                        "url": row["url"],
                        "excerpt": row.get("body"),
                        "published_at": row.get("date"),
                        "retrieved_at": retrieved,
                        "access": "search_snippet",
                        "note": "Public discussion, not a verified event",
                    }
                )
            return posts, None

        outcomes = await asyncio.gather(*(discover(platform) for platform in selected))
        return json.dumps(
            {
                "topic": topic[:120],
                "posts": [post for posts, _ in outcomes for post in posts],
                "errors": [error for _, error in outcomes if error],
            },
            ensure_ascii=False,
        )

    async def curate_public_sources(self, asset: str, topics: list[str], lookback_hours: int = 48) -> str:
        """Collect public official, crypto, calendar, and expectation source cards for BTC or ETH."""
        try:
            self.budget.consume("curate_public_sources")
        except (TimeoutError, ValueError) as error:
            return json.dumps({"sources": [], "errors": [{"source": "curation", "error": str(error)}]})
        asset = asset.upper()
        if asset not in {"BTC", "ETH"}:
            return json.dumps({"sources": [], "errors": [{"source": "curation", "error": "asset must be BTC or ETH"}]})
        lookback_hours = max(1, min(lookback_hours, 168))
        sources = _sources(asset, topics)
        retrieved = datetime.now(UTC).isoformat()
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False) as client:

            async def collect(source: Source) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
                started = time.perf_counter()
                try:
                    async with semaphore, asyncio.timeout(self.budget.remaining(35)):
                        fetched = await fetch_public_document(
                            source.url, self.settings, client=client, accepted_types=SOURCE_TYPES
                        )
                        body = fetched.body
                        if source.kind == "calendar":
                            cards = parse_calendar(body, source, retrieved)
                        elif source.name == "Polymarket":
                            cards = parse_polymarket(body, source, retrieved)
                        elif source.name.startswith("GDELT"):
                            cards = parse_gdelt(body, source, retrieved)
                        elif source.name in {"Binance announcements", "Bitcoin Core"}:
                            cards = parse_listing(body, source, retrieved)
                        else:
                            cards = parse_feed(body, source, retrieved)
                    if not cards:
                        return [], {
                            "source": source.name,
                            "error": "Source returned no usable items",
                            "elapsed_ms": str(round((time.perf_counter() - started) * 1000)),
                        }
                    return cards, None
                except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError) as error:
                    return [], {
                        "source": source.name,
                        "error": (str(error) or type(error).__name__)[:180],
                        "elapsed_ms": str(round((time.perf_counter() - started) * 1000)),
                    }

            outcomes = await asyncio.gather(*(collect(source) for source in sources))
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=lookback_hours)
        calendar_end = now + timedelta(days=7)
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        errors = []
        for items, error in outcomes:
            if error:
                errors.append(error)
            count = 0
            for item in items:
                if item["url"] in seen and item["kind"] != "calendar":
                    continue
                published = item["published_at"]
                older = False
                if published:
                    try:
                        when = datetime.fromisoformat(published)
                        if when.tzinfo:
                            if item["kind"] == "calendar" and not (cutoff <= when <= calendar_end):
                                continue
                            older = when < cutoff
                    except ValueError:
                        pass
                if older:
                    item["older_than_window"] = True
                    if count >= 1 and item["kind"] != "expectations":
                        continue
                seen.add(item["url"])
                cards.append(item)
                count += 1
                if count >= (1 if older and item["kind"] != "expectations" else 4):
                    break
        return json.dumps(
            {
                "asset": asset,
                "retrieved_at": retrieved,
                "sources": cards,
                "errors": errors,
                "source_count": len(sources),
            },
            ensure_ascii=False,
        )
