from __future__ import annotations

import asyncio
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import httpx
from agno.tools.websearch import WebSearchTools as AgnoWebSearchTools
from bs4 import BeautifulSoup
from ddgs.exceptions import DDGSException

from .budget import ResearchBudget
from .tools import canonicalize_url

logger = logging.getLogger(__name__)


class WebSearchTools(AgnoWebSearchTools):
    """Native keyless Agno search with bounded results, calls, and elapsed time."""

    def __init__(self, budget: ResearchBudget | None = None, *, timelimit: str | None = None) -> None:
        self.budget = budget or ResearchBudget()
        self.cache: dict[tuple[str, str, int], str] = {}
        super().__init__(timeout=10, fixed_max_results=10, timelimit=timelimit)

    async def _google_news(self, query: str, limit: int) -> list[dict]:
        """Use Google's public RSS as a discovery fallback, not as a citable article."""
        params = urlencode({"q": f"{query} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        url = f"https://news.google.com/rss/search?{params}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), follow_redirects=True) as client:
            async with asyncio.timeout(self.budget.remaining(9)):
                response = await client.get(url)
                response.raise_for_status()
        items = ET.fromstring(response.content).findall(".//item")
        results = []
        for item in items[:limit]:
            link = item.findtext("link")
            title = item.findtext("title")
            if not link or not title:
                continue
            published = item.findtext("pubDate")
            try:
                published = parsedate_to_datetime(published).astimezone(UTC).isoformat() if published else None
            except (TypeError, ValueError):
                published = None
            results.append(
                {
                    "url": canonicalize_url(link),
                    "title": title[:300],
                    "body": BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)[
                        :500
                    ],
                    "date": published,
                    "source": "Google News",
                    "publisher": item.findtext("source"),
                    "access": "discovery_snippet",
                }
            )
        return results

    @staticmethod
    def _recent_count(rows: list[dict]) -> int:
        now = datetime.now(UTC)
        count = 0
        for row in rows:
            try:
                published = datetime.fromisoformat(str(row.get("date") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if published.tzinfo and 0 <= (now - published).total_seconds() <= 72 * 3600:
                count += 1
        return count

    async def _search(self, name: str, query: str, max_results: int | None) -> str:
        started = time.perf_counter()
        try:
            self.budget.consume(name)
            limit = max(1, min(max_results or 10, 10))
            query = " ".join(query.split())[:500]
            key = (name, query.casefold(), limit)
            if key in self.cache:
                return self.cache[key]
            method = super().search_news if name == "search_news" else super().web_search
            provider_error = None
            try:
                async with asyncio.timeout(self.budget.remaining(20)):
                    rows = json.loads(await asyncio.to_thread(method, query, max_results=limit))
            except (DDGSException, TimeoutError, ValueError) as exc:
                provider_error = str(exc) or type(exc).__name__
                rows = []
            results = []
            seen: set[str] = set()
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                url = str(row.get("url") or row.get("href") or "")
                if not url.startswith(("https://", "http://")):
                    continue
                url = canonicalize_url(url)
                if url in seen:
                    continue
                seen.add(url)
                results.append(
                    {
                        "url": url,
                        "title": str(row.get("title") or "")[:300],
                        "body": str(row.get("body") or row.get("description") or "")[:700],
                        "date": row.get("date"),
                        "source": row.get("source"),
                    }
                )
                if len(results) == limit:
                    break
            if name == "search_news" and self._recent_count(results) < min(3, limit):
                try:
                    discovered = await self._google_news(query, limit)
                    primary = discovered[: max(1, limit // 2)] if results else discovered
                    remainder = discovered[len(primary) :]
                    results = list(dict((row["url"], row) for row in [*primary, *results, *remainder]).values())[:limit]
                except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError) as exc:
                    logger.warning(
                        "Google News discovery failed query=%r reason=%s", query, str(exc) or type(exc).__name__
                    )
            if provider_error:
                logger.warning("News search provider failed tool=%s query=%r reason=%s", name, query, provider_error)
                if not results:
                    return json.dumps({"ok": False, "error": provider_error[:160], "results": []})
            result = json.dumps(results, ensure_ascii=False, separators=(",", ":"))
            self.cache[key] = result
            return result
        except (TimeoutError, ValueError) as exc:
            return json.dumps({"ok": False, "error": str(exc), "results": []})
        except Exception:
            logger.exception("News search failed tool=%s", name)
            return json.dumps({"ok": False, "error": "Search provider unavailable", "results": []})
        finally:
            logger.info("news.tool name=%s elapsed_ms=%d", name, round((time.perf_counter() - started) * 1000))

    async def search_news(self, query: str, max_results: int = 10) -> str:
        """Search current news. At most ten results and ten calls per analysis."""
        return await self._search("search_news", query, max_results)

    async def web_search(self, query: str, max_results: int = 10) -> str:
        """Search official sources or fill a specific evidence gap within the run budget."""
        return await self._search("web_search", query, max_results)
