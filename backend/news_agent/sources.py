"""Public source discovery for the news analyst, without event or trade decisions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from agno.tools import Toolkit
from bs4 import BeautifulSoup

from .budget import ResearchBudget
from .config import NewsAgentSettings
from .events import release_comparison
from .search import WebSearchTools
from .social import discussion_evidence
from .source_cache import SOURCE_CACHE, SourceSnapshot
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
    replacement_for: str | None = None


SOURCES = (
    Source("Federal Reserve monetary policy", "official", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    Source("Federal Reserve releases", "official", "https://www.federalreserve.gov/feeds/press_all.xml"),
    Source("SEC releases", "official", "https://www.sec.gov/rss/news/press.xml"),
    Source("BEA releases", "official", "https://apps.bea.gov/rss/rss.xml"),
    Source("Treasury releases", "official_listing", "https://home.treasury.gov/news/press-releases"),
    Source("IRS tax releases", "official_listing", "https://www.irs.gov/newsroom/news-releases-for-current-month"),
    Source("OFAC sanctions", "official_listing", "https://ofac.treasury.gov/recent-actions"),
    Source("EU sanctions guidance", "official", "https://finance.ec.europa.eu/node/1296/rss_en"),
    Source("CFTC releases", "official", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    Source("White House actions", "official_listing", "https://www.whitehouse.gov/presidential-actions/"),
    Source("White House statements", "official_listing", "https://www.whitehouse.gov/briefings-statements/"),
    Source("ECB releases", "official", "https://www.ecb.europa.eu/rss/press.html"),
    Source("Bank of England news", "official", "https://www.bankofengland.co.uk/rss/news"),
    Source("Bank of Japan news", "official", "https://www.boj.or.jp/en/rss/whatsnew.xml"),
    Source("Bank of Japan Japanese", "official", "https://www.boj.or.jp/rss/whatsnew.xml"),
    Source("BIS releases", "official", "https://www.bis.org/doclist/all_pressrels.rss"),
    Source("Coinbase status", "organization_status", "https://status.coinbase.com/history.rss"),
    Source("Kraken status", "organization_status", "https://status.kraken.com/history.rss"),
    Source("Circle status", "organization_status", "https://status.circle.com/history.rss"),
    Source("BLS calendar", "calendar", "https://www.bls.gov/schedule/news_release/bls.ics"),
    Source("Forex Factory calendar", "calendar", "https://nfs.faireconomy.media/ff_calendar_thisweek.json"),
    Source("CoinDesk", "publisher", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    Source("Decrypt", "publisher", "https://decrypt.co/feed"),
    Source("Blockworks", "publisher", "https://blockworks.co/feed"),
    Source("The Block", "publisher", "https://www.theblock.co/rss.xml"),
    Source("Kraken announcements", "organization", "https://blog.kraken.com/feed/"),
    Source("Delta announcements", "organization_listing", "https://www.delta.exchange/blog"),
    Source("Cointelegraph Bitcoin", "publisher", "https://cointelegraph.com/rss/tag/bitcoin", ("BTC",)),
    Source("Cointelegraph Ethereum", "publisher", "https://cointelegraph.com/rss/tag/ethereum", ("ETH",)),
    Source("Ethereum Foundation", "organization", "https://blog.ethereum.org/en/feed.xml", ("ETH",)),
    Source("Binance announcements", "organization", "https://www.binance.com/en/support/announcement/list/000"),
    Source("Bitcoin Core", "organization", "https://bitcoin.org/en/version-history", ("BTC",)),
)
DISCUSSION_SITES = {
    "reddit": "reddit.com",
    "x": "x.com",
    "threads": "threads.com",
    "facebook": "facebook.com",
    "binance_square": "binance.com/en/square",
}
EXCHANGE_SITES = {
    "binance": "binance.com/en/support/announcement",
    "coinbase": "coinbase.com/blog",
    "kraken": "blog.kraken.com",
    "okx": "okx.com/help",
    "bybit": "announcements.bybit.com",
    "delta": "delta.exchange/blog",
}
DISCOVERY_FALLBACKS = {
    "Decrypt": "site:decrypt.co Bitcoin OR Ethereum",
    "Binance announcements": "site:binance.com/en/support/announcement/detail/",
    "CFTC releases": "site:cftc.gov/PressRoom/PressReleases crypto OR derivatives",
    "Polymarket": "site:polymarket.com/event/ Bitcoin OR Ethereum",
    "Blockworks": "site:blockworks.com/news Bitcoin OR Ethereum",
    "GDELT asset": "Bitcoin OR Ethereum",
    "GDELT global": '"central bank" OR inflation OR tariffs OR geopolitics',
}


def _matches_site(url: str, site: str) -> bool:
    """Check actual result provenance instead of trusting the search engine's site operator."""
    try:
        actual, expected = urlsplit(url), urlsplit(f"https://{site}")
    except ValueError:
        return False
    host, domain = (actual.hostname or "").rstrip("."), expected.hostname or ""
    path = expected.path.rstrip("/")
    return (
        actual.scheme in {"http", "https"}
        and (host == domain or host.endswith(f".{domain}"))
        and (not path or actual.path == path or actual.path.startswith(path + "/"))
    )


def select_source_cards(
    items: list[dict[str, Any]], source: Source, now: datetime, lookback_hours: int
) -> list[dict[str, Any]]:
    """Select dated evidence before limiting; calendars prioritize relevant high-impact events."""
    cutoff = now - timedelta(hours=lookback_hours)
    recent, background = [], []
    for item in items:
        when = None
        with suppress(ValueError, TypeError):
            when = datetime.fromisoformat(item.get("published_at") or "")
        if when is not None and when.tzinfo is None:
            when = None
        item["publication_status"] = "dated" if when else "undated"
        if source.kind == "calendar":
            if when and cutoff <= when <= now + timedelta(days=7):
                recent.append(item)
        elif when and when < cutoff:
            item["older_than_window"] = True
            background.append(item)
        elif when and when > now + timedelta(minutes=5):
            item["publication_status"] = "future_date"
            background.append(item)
        else:
            recent.append(item)
    if source.kind == "calendar":
        impact = {"High": 0, "Medium": 1, "Low": 2, "Holiday": 3}
        recent.sort(
            key=lambda item: (
                impact.get(item.get("impact"), 2),
                item.get("country") != "USD",
                abs((datetime.fromisoformat(item["published_at"]) - now).total_seconds()),
            )
        )
        return recent[:16]
    # Feeds and listings do not necessarily arrive newest first. Unknown dates remain behind dated leads.
    recent.sort(
        key=lambda item: (item.get("publication_status") != "undated", item.get("published_at") or ""), reverse=True
    )
    background.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    # A stale article may provide background, but never substitutes for a recent feed item.
    return recent[:4] if recent else background[:1]


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True).split())[:limit]


def _date(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    # IRS release teasers use abbreviated month names such as Oct. 23, 2026.
    raw = re.sub(r"^([A-Z][a-z]{2})\.(?= )", r"\1", raw)
    for pattern in ("%B %d, %Y", "%b %d, %Y"):
        with suppress(ValueError):
            return datetime.strptime(raw, pattern).replace(tzinfo=UTC).isoformat()
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
    items = (
        root.findall(".//item")
        or root.findall("{http://www.w3.org/2005/Atom}entry")
        or root.findall("{http://purl.org/rss/1.0/}item")
    )
    cards = []
    for item in items[:50]:

        def field(*names: str, current: ET.Element = item) -> str | None:
            for name in names:
                element = current.find(name)
                if element is not None and element.text:
                    return element.text
            return None

        links = [
            *item.findall("link"),
            *item.findall("{http://www.w3.org/2005/Atom}link"),
            *item.findall("{http://purl.org/rss/1.0/}link"),
        ]
        link = next((link for link in links if link.get("rel", "alternate") == "alternate"), None)
        href = link.get("href") or link.text if link is not None else None
        if not href:
            continue
        card = _card(
            source=source,
            title=field("title", "{http://www.w3.org/2005/Atom}title", "{http://purl.org/rss/1.0/}title"),
            url=href,
            published=field(
                "pubDate",
                "{http://purl.org/dc/elements/1.1/}date",
                "{http://www.w3.org/2005/Atom}published",
            ),
            excerpt=field(
                "description", "{http://www.w3.org/2005/Atom}summary", "{http://purl.org/rss/1.0/}description"
            ),
            retrieved=retrieved,
            extra=(
                {
                    "publisher": field("source"),
                    "note": "Discovery lead; open the publisher article before citing it",
                }
                if source.kind == "discovery"
                else None
            ),
        )
        if card:
            card["source_updated_at"] = _date(field("{http://www.w3.org/2005/Atom}updated"))
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
                extra={
                    "country": entry.get("country"),
                    "impact": entry.get("impact"),
                    "forecast": entry.get("forecast"),
                    "actual": entry.get("actual"),
                    "previous": entry.get("previous"),
                    **{
                        key: entry[key]
                        for key in (
                            "unit",
                            "reference_period",
                            "revised_previous",
                            "actual_unit",
                            "forecast_unit",
                            "actual_period",
                            "forecast_period",
                        )
                        if key in entry
                    },
                },
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
                extra={
                    "country": "USD",
                    "impact": "High"
                    if event.get("SUMMARY") in {"Consumer Price Index", "Producer Price Index", "Employment Situation"}
                    else "Medium",
                },
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
        path = urljoin(source.url, href)
        if source.name.startswith("White House") and not re.search(
            r"/(presidential-actions|briefings-statements)/\d{4}/\d{2}/[^/]+", path
        ):
            continue
        if source.name == "Treasury releases" and not re.search(r"/news/press-releases/[a-z]+\d+/?$", path):
            continue
        if source.name == "OFAC sanctions" and not re.search(r"/recent-actions/\d{8}[^/?#]*/?$", path):
            continue
        if source.name == "IRS tax releases" and (
            not link.find_parent(["h2", "h3"]) or "bookmark" not in link.get("rel", [])
        ):
            continue
        if source.name == "Delta announcements" and not re.search(r"/blog/[^/?#]+", path):
            continue
        if source.name == "Binance announcements" and "/support/announcement/detail/" not in href:
            continue
        if source.name == "Bitcoin Core" and "/en/releases/" not in href:
            continue
        title = link.get_text(" ", strip=True)
        published = None
        container = (
            link.find_parent("article")
            or link.find_parent("li")
            or link.find_parent(class_=re.compile(r"views-row|featured-stories__item"))
            or link.parent
        )
        if source.name == "Treasury releases":
            heading = link.find_parent(["h2", "h3"])
            if heading:
                container = heading.parent
        if source.name == "IRS tax releases":
            container = link.find_parent(class_=re.compile(r"^field--item$|views-row")) or container
        if source.name == "OFAC sanctions":
            container = link.find_parent("tr") or link.find_parent(class_=re.compile(r"views-row")) or container
        if container:
            dated = container.find("time")
            if dated:
                published = dated.get("datetime") or dated.get_text(" ", strip=True)
            if not published:
                date_text = re.search(r"[A-Z][a-z]+\.? \d{1,2}, \d{4}", container.get_text(" ", strip=True))
                published = date_text.group() if date_text else None
        if source.name == "Delta announcements":
            heading = link.select_one('[class*="cardTitle"]')
            if heading:
                title = heading.get_text(" ", strip=True)
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
    if not isinstance(data, dict) or not isinstance(data.get("articles", []), list):
        raise ValueError("GDELT response must contain an article list")
    cards = []
    for item in data.get("articles", [])[:30]:
        if not isinstance(item, dict):
            continue
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
    if not isinstance(data, dict) or not isinstance(data.get("events", []), list):
        raise ValueError("Prediction response must contain an event list")
    cards = []
    for item in data.get("events", [])[:12]:
        if not isinstance(item, dict):
            continue
        markets = item.get("markets") or []
        detail = _text(item.get("description"), 300)
        if isinstance(markets, list) and markets and isinstance(markets[0], dict):
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
    for label, query in (
        (
            "Crypto law and regulation",
            '(cryptocurrency OR "digital assets") (regulation OR law OR court OR ban OR MiCA)',
        ),
        ("Tax and fiscal policy", '(cryptocurrency OR "digital assets" OR budget) (tax OR taxation OR revenue)'),
        (
            "Country economic reports",
            '(GDP OR inflation OR employment OR "fiscal monitor" OR "financial stability") '
            "(site:imf.org OR site:worldbank.org OR site:oecd.org OR site:gov.uk)",
        ),
        (
            "Trade tariffs and sanctions",
            '(tariffs OR sanctions OR "export controls" OR "import ban") (trade OR economy OR finance)',
        ),
        (
            "War energy and shipping",
            '(war OR ceasefire OR "military escalation" OR blockade) (oil OR energy OR shipping OR markets)',
        ),
        (
            "Regional crypto policy",
            '(cryptocurrency OR "digital assets") (India OR China OR Japan OR "European Union") '
            "(law OR ban OR tax OR regulation)",
        ),
        ("Institutional disclosures", f"{asset} (site:sec.gov OR site:blackrock.com OR site:fidelity.com)"),
        ("Crypto infrastructure", f"{asset} (stablecoin OR exploit OR withdrawal OR custody OR network upgrade)"),
        ("Regional macro", "(site:boj.or.jp OR site:imf.org OR site:ecb.europa.eu) monetary policy"),
    ):
        params = urlencode({"q": f"{query} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        selected.append(Source(label, "discovery", f"https://news.google.com/rss/search?{params}"))
    # Bound free text and quote it as a phrase rather than accepting search operators.
    terms = list(dict.fromkeys(" ".join(re.findall(r"[\w-]+", topic))[:80] for topic in topics[:4]))
    terms = [term for term in terms if term]
    if terms:
        topic_query = f"{asset} (" + " OR ".join(f'"{term}"' for term in terms) + ") when:2d"
        params = urlencode({"q": topic_query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
        selected.append(Source("Google News topics", "discovery", f"https://news.google.com/rss/search?{params}"))
    for label, term in (
        ("asset", "Ethereum ETH staking ETF" if asset == "ETH" else "Bitcoin BTC ETF"),
        ("global", '"Federal Reserve" OR inflation OR tariffs OR sanctions OR geopolitics'),
    ):
        params = urlencode({"q": f"{term} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        selected.append(Source(f"Google News {label}", "discovery", f"https://news.google.com/rss/search?{params}"))
    for label, term in (
        ("asset", "Ethereum OR ETH" if asset == "ETH" else "Bitcoin OR BTC"),
        ("global", '"central bank" OR inflation OR geopolitics'),
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
    if os.getenv("NEWS_TRY_UNRELIABLE_SOURCES", "false").lower() != "true":
        replacements = {"Binance announcements", "GDELT asset", "GDELT global", "Polymarket"}
        for index, source in enumerate(selected):
            if source.name in replacements:
                query = DISCOVERY_FALLBACKS[source.name]
                params = urlencode({"q": f"({query}) when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
                selected[index] = Source(
                    f"{source.name} discovery",
                    "discovery",
                    f"https://news.google.com/rss/search?{params}",
                    replacement_for=source.url,
                )
    return selected


class SourceParseError(ValueError):
    """A retrieved document did not satisfy its connector format."""


def parse_source_document(body: bytes, source: Source, retrieved: str) -> list[dict[str, Any]]:
    try:
        if source.kind == "calendar":
            return parse_calendar(body, source, retrieved)
        if source.kind == "expectations" and source.name == "Polymarket":
            return parse_polymarket(body, source, retrieved)
        if source.kind == "discovery" and source.replacement_for:
            return parse_feed(body, source, retrieved)
        if source.name in {"GDELT asset", "GDELT global"}:
            return parse_gdelt(body, source, retrieved)
        if source.kind.endswith("_listing") or source.name in {"Binance announcements", "Bitcoin Core"}:
            return parse_listing(body, source, retrieved)
        return parse_feed(body, source, retrieved)
    except (ValueError, ET.ParseError) as error:
        raise SourceParseError(str(error) or "Unusable source document") from error


class PublicSourceTools(Toolkit):
    def __init__(self, settings: NewsAgentSettings, budget: ResearchBudget, **kwargs: Any) -> None:
        self.settings = settings
        self.budget = budget
        self.source_index: dict[str, dict[str, str | None]] = {}
        self.search = WebSearchTools(budget, timelimit="w")
        super().__init__(
            name="public_source_tools",
            tools=[self.curate_public_sources, self.search_public_discussion, self.search_exchange_announcements],
            **kwargs,
        )

    async def search_exchange_announcements(self, topic: str, exchanges: list[str]) -> str:
        """Find public exchange announcements for later reading at their original URLs."""
        try:
            self.budget.consume("search_exchange_announcements")
        except (TimeoutError, ValueError) as error:
            return json.dumps({"announcements": [], "errors": [{"exchange": "search", "error": str(error)}]})
        selected = list(dict.fromkeys(name.lower() for name in exchanges if name.lower() in EXCHANGE_SITES))[:6]
        if not selected:
            selected = list(EXCHANGE_SITES)

        async def discover(exchange: str) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
            query = f"site:{EXCHANGE_SITES[exchange]} {topic[:120]}"
            result = json.loads(await self.search.web_search(query, max_results=5))
            if not isinstance(result, list):
                return [], {"exchange": exchange, "error": str(result.get("error") or "Search unavailable")}
            return [
                {
                    "exchange": exchange,
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "excerpt": row.get("body"),
                    "search_date": row.get("date"),
                    "reported_date": row.get("reported_date"),
                    "publication_status": row.get("publication_status"),
                    "age_hours": row.get("age_hours"),
                    "retrieved_at": row.get("retrieved_at"),
                    "access": "search_snippet",
                }
                for row in result
                if isinstance(row, dict) and _matches_site(str(row.get("url") or ""), EXCHANGE_SITES[exchange])
            ], None

        outcomes = await asyncio.gather(*(discover(exchange) for exchange in selected))
        return json.dumps(
            {
                "topic": topic[:120],
                "announcements": [item for items, _ in outcomes for item in items],
                "errors": [error for _, error in outcomes if error],
            },
            ensure_ascii=False,
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
        collected = discussion_evidence(topic, selected)
        sampled = {
            platform for platform in selected if sum(post["platform"] == platform for post in collected["posts"]) >= 3
        }

        async def discover(platform: str) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
            query = f"site:{DISCUSSION_SITES[platform]} {topic[:120]}"
            result = json.loads(await self.search.web_search(query, max_results=5))
            if not isinstance(result, list):
                return [], {"platform": platform, "error": str(result.get("error") or "Search unavailable")}
            posts = []
            for row in result:
                if not isinstance(row, dict) or not _matches_site(
                    str(row.get("url") or ""), DISCUSSION_SITES[platform]
                ):
                    continue
                posts.append(
                    {
                        "platform": platform,
                        "kind": "discussion",
                        "title": row.get("title"),
                        "url": row["url"],
                        "excerpt": row.get("body"),
                        "published_at": row.get("date"),
                        "reported_date": row.get("reported_date"),
                        "publication_status": row.get("publication_status"),
                        "age_hours": row.get("age_hours"),
                        "retrieved_at": retrieved,
                        "access": "search_snippet",
                        "note": "Public discussion, not a verified event",
                    }
                )
            return posts, None

        outcomes = await asyncio.gather(*(discover(platform) for platform in selected if platform not in sampled))
        return json.dumps(
            {
                "topic": topic[:120],
                "posts": [*collected["posts"], *[post for posts, _ in outcomes for post in posts]],
                "errors": [*collected["errors"], *[error for _, error in outcomes if error]],
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
                async def load() -> SourceSnapshot:
                    checked = datetime.now(UTC).isoformat()
                    try:
                        async with asyncio.timeout(self.budget.remaining(12)):
                            fetched = await fetch_public_document(
                                source.url, self.settings, client=client, accepted_types=SOURCE_TYPES
                            )
                            cards = parse_source_document(fetched.body, source, checked)
                        return SourceSnapshot(
                            cards,
                            checked,
                            None if cards else "Source returned no usable items",
                            None if cards else "empty",
                            60 if cards else 120,
                        )
                    except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError) as error:
                        code, retry = "fetch_failed", 120.0
                        if isinstance(error, SourceParseError):
                            code = "parse_failed"
                        elif isinstance(error, httpx.HTTPStatusError):
                            status = error.response.status_code
                            code = (
                                "rate_limited"
                                if status == 429
                                else "blocked"
                                if status in {401, 403}
                                else f"http_{status}"
                            )
                            retry = 900 if code == "blocked" else 120
                            if status == 429:
                                with suppress(ValueError):
                                    retry = max(120, min(3600, float(error.response.headers.get("retry-after", "120"))))
                        elif isinstance(error, (TimeoutError, httpx.TimeoutException)):
                            code = "timeout"
                        return SourceSnapshot([], checked, (str(error) or type(error).__name__)[:180], code, retry)

                try:
                    async with semaphore, asyncio.timeout(self.budget.remaining(25)):
                        snapshot = await SOURCE_CACHE.get(
                            (4, source.url, source.kind, tuple(getattr(self.settings, "allowed_domains", ()))), load
                        )
                except TimeoutError:
                    snapshot = SourceSnapshot([], retrieved, "Source collection deadline", "timeout", 120)
                error = (
                    {"source": source.name, "error": snapshot.error, "code": snapshot.error_code}
                    if snapshot.error
                    else None
                )
                selected = select_source_cards(snapshot.cards, source, datetime.now(UTC), lookback_hours)
                status = snapshot.error_code or (
                    "stale"
                    if selected and all(item.get("older_than_window") for item in selected)
                    else "no_current_items"
                    if not selected
                    else "future_dated"
                    if all(item.get("publication_status") == "future_date" for item in selected)
                    else "undated"
                    if all(item.get("publication_status") == "undated" for item in selected)
                    else "available"
                )
                health.append(
                    {
                        "source": source.name,
                        "kind": source.kind,
                        "replacement_for": source.replacement_for,
                        "original_status": "not_attempted_prior_failures" if source.replacement_for else None,
                        "url": source.url,
                        "status": status,
                        "checked_at": snapshot.checked_at,
                        "last_success_at": snapshot.last_success_at,
                        "returned_items": len(snapshot.cards),
                        "selected_items": len(selected),
                        "newest_published_at": max(
                            (item.get("published_at") or "" for item in snapshot.cards), default=""
                        )
                        or None,
                    }
                )
                if (snapshot.error or status == "stale") and source.name in DISCOVERY_FALLBACKS:
                    # Public search is an alternate discovery route, never an access-control bypass.
                    query = DISCOVERY_FALLBACKS[source.name]
                    params = urlencode({"q": f"{query} when:7d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
                    fallback = Source(
                        f"{source.name} discovery", "discovery", f"https://news.google.com/rss/search?{params}"
                    )
                    leads, fallback_error = await collect(fallback)
                    if not fallback_error:
                        selected.extend(leads)
                return selected, error

            health: list[dict[str, Any]] = []
            tasks = [asyncio.create_task(collect(source)) for source in sources]
            try:
                done, pending = (
                    await asyncio.wait(tasks, timeout=self.budget.remaining(45)) if tasks else (set(), set())
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                outcomes = []
                for source, task in zip(sources, tasks, strict=True):
                    if task in done and not task.cancelled():
                        outcomes.append(task.result())
                    else:
                        error = {
                            "source": source.name,
                            "error": "Source collection deadline or cancellation",
                            "code": "timeout",
                        }
                        outcomes.append(([], error))
                        health.append(
                            {
                                "source": source.name,
                                "kind": source.kind,
                                "url": source.url,
                                "status": "timeout",
                                "checked_at": datetime.now(UTC).isoformat(),
                                "selected_items": 0,
                            }
                        )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        errors = []
        for items, error in outcomes:
            if error:
                errors.append(error)
            for item in items:
                if item["url"] in seen and item["kind"] != "calendar":
                    continue
                seen.add(item["url"])
                cards.append(item)
                if item["kind"] == "calendar":
                    item["release_comparison"] = release_comparison(item)
                    item["evidence_text"] = (
                        f"{item['title']}\n{item.get('excerpt') or ''}\nScheduled_at: {item.get('published_at')}"
                    )
                    item["evidence_id"] = hashlib.sha256((item["url"] + item["evidence_text"]).encode()).hexdigest()[
                        :20
                    ]
                self.source_index[item["url"]] = {
                    "source": item["source"],
                    "published_at": item["published_at"],
                }
        return json.dumps(
            {
                "asset": asset,
                "retrieved_at": retrieved,
                "sources": cards,
                "errors": errors,
                "source_count": len(sources),
                "source_health": sorted(health, key=lambda item: item["source"]),
                "budget": self.budget.snapshot(),
                "calendar_note": "Calendar entries are scheduled times, not confirmation that a release occurred. "
                "Use official delay notices over a calendar entry; "
                "an unverified delay remains uncertain for both assets.",
            },
            ensure_ascii=False,
        )
