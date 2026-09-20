from __future__ import annotations

import asyncio
import json
import logging
import time

from agno.tools.websearch import WebSearchTools as AgnoWebSearchTools

from .budget import ResearchBudget
from .tools import canonicalize_url

logger = logging.getLogger(__name__)


class WebSearchTools(AgnoWebSearchTools):
    """Native keyless Agno search with bounded results, calls, and elapsed time."""

    def __init__(self, budget: ResearchBudget | None = None) -> None:
        self.budget = budget or ResearchBudget()
        self.cache: dict[tuple[str, str, int], str] = {}
        super().__init__(timeout=10, fixed_max_results=10, timelimit="d")

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
            async with asyncio.timeout(self.budget.remaining(20)):
                raw = await asyncio.to_thread(method, query, max_results=limit)
            rows = json.loads(raw)
            results = []
            seen: set[str] = set()
            for row in rows:
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
        """Search official sources or fill a specific evidence gap. At most ten calls per analysis."""
        return await self._search("web_search", query, max_results)
