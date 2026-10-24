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
from .freshness import publication_metadata
from .tools import canonicalize_url

logger = logging.getLogger(__name__)


class WebSearchTools(AgnoWebSearchTools):
    """Native keyless Agno search with bounded results, calls, and elapsed time."""

    def __init__(self, budget: ResearchBudget | None = None, *, timelimit: str | None = None) -> None:
        self.budget = budget or ResearchBudget()
        self.cache: dict[tuple[str, str, int], str] = {}
        self.cache_expires: dict[tuple[str, str, int], float] = {}
        self.pending: dict[tuple[str, str, int], asyncio.Task] = {}
        self.search_slots = asyncio.Semaphore(4)
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
            if published.tzinfo and 0 <= (now - published).total_seconds() <= 48 * 3600:
                count += 1
        return count

    async def _search(self, name: str, query: str, max_results: int | None) -> str:
        limit = max(1, min(max_results or 10, 10))
        query = " ".join(query.split())[:500]
        key = (name, query.casefold(), limit)
        if key in self.cache and self.cache_expires.get(key, 0) > time.monotonic():
            return self.cache[key]
        if key not in self.pending:
            task = asyncio.create_task(self._execute_search(name, query, limit))
            self.pending[key] = task

            def finished(done):
                if self.pending.get(key) is done:
                    self.pending.pop(key, None)

            task.add_done_callback(finished)
        # Cancelling one waiter must not discard another caller's in-flight search.
        return await asyncio.shield(self.pending[key])

    async def _execute_search(self, name: str, query: str, limit: int) -> str:
        started = time.perf_counter()
        key = (name, query.casefold(), limit)
        try:
            self.budget.consume(name)
            method = super().search_news if name == "search_news" else super().web_search
            provider_error = None
            try:
                async with asyncio.timeout(self.budget.remaining(20)), self.search_slots:
                    rows = json.loads(await asyncio.to_thread(method, query, max_results=limit))
            except (DDGSException, TimeoutError, ValueError) as exc:
                provider_error = str(exc) or type(exc).__name__
                rows = []
                if provider_error.casefold().rstrip(".") == "no results found":
                    provider_error = None
                    # Date filters often exclude indexed official notices and public posts.
                    # Undated results are still discovery leads, never proof of current events.
                    if name == "web_search" and "site:" in query and self.timelimit:
                        try:
                            fallback = AgnoWebSearchTools(timeout=8, fixed_max_results=limit, timelimit=None)
                            async with asyncio.timeout(self.budget.remaining(10)):
                                rows = json.loads(
                                    await asyncio.to_thread(fallback.web_search, query, max_results=limit)
                                )
                        except (DDGSException, TimeoutError, ValueError) as fallback_error:
                            reason = str(fallback_error) or type(fallback_error).__name__
                            if reason.casefold().rstrip(".") != "no results found":
                                provider_error = reason
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
                    results = list(dict((row["url"], row) for row in [*primary, *results, *remainder]).values())
                except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError) as exc:
                    logger.warning(
                        "Google News discovery failed query=%r reason=%s", query, str(exc) or type(exc).__name__
                    )
            if provider_error:
                logger.warning("News search provider failed tool=%s query=%r reason=%s", name, query, provider_error)
                if not results:
                    return json.dumps({"ok": False, "error": provider_error[:160], "results": []})
            now = datetime.now(UTC)
            for row in results:
                date = publication_metadata(row.get("date"), now)
                row.update(
                    {
                        "date": date["published_at"],
                        "reported_date": date["reported_published_at"],
                        "publication_status": date["publication_status"],
                        "age_hours": date["age_hours"],
                        "retrieved_at": now.isoformat(),
                        "access": "discovery_snippet",
                    }
                )
                for field, length in (("title", 300), ("body", 700)):
                    row[field] = " ".join(
                        BeautifulSoup(str(row.get(field) or ""), "html.parser").get_text(" ", strip=True).split()
                    )[:length]
            freshness_order = {"recent": 0, "undated": 1, "stale": 2, "future_date": 3}
            results.sort(key=lambda row: (freshness_order[row["publication_status"]], row["age_hours"] or 0))
            result = json.dumps(results[:limit], ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            self.cache[key] = result
            self.cache_expires[key] = time.monotonic() + (60 if results else 10)
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
