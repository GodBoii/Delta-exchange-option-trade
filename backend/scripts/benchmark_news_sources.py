"""Audit observed article URLs and live keyless search without model calls or credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from news_agent.config import NewsAgentSettings
from news_agent.search import WebSearchTools
from news_agent.tools import canonicalize_url, deduplicate_articles, fetch_public_document, parse_article_html


async def audit(log: Path, output: Path) -> None:
    urls = set()
    if log.exists():
        for match in re.finditer(
            r"(?:final_url|tool\.read_news_article start url)=(https?://[^\s]+)",
            log.read_text(encoding="utf-8", errors="replace"),
        ):
            urls.add(canonicalize_url(match[1]))
    search = WebSearchTools()
    started = time.perf_counter()
    results = json.loads(await search.search_news("Bitcoin BTC latest news"))
    search_ms = round((time.perf_counter() - started) * 1000)
    if isinstance(results, list):
        urls.update(row["url"] for row in results)
    settings = NewsAgentSettings.load()
    slots = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False) as client:

        async def measure(url: str) -> dict:
            async with slots:
                start = time.perf_counter()
                try:
                    fetched = await fetch_public_document(url, settings, client=client)
                    fetched_at = time.perf_counter()
                    article = await asyncio.to_thread(
                        parse_article_html,
                        fetched.body.decode("utf-8", errors="replace"),
                        fetched.final_url,
                        8000,
                    )
                    parsed_at = time.perf_counter()
                    serialized = json.dumps(article)
                    return {
                        "url": url,
                        "ok": len(article["text"]) >= 200,
                        "bytes": len(fetched.body),
                        "fetch_ms": round((fetched_at - start) * 1000),
                        "parse_ms": round((parsed_at - fetched_at) * 1000),
                        "serialize_ms": round((time.perf_counter() - parsed_at) * 1000, 2),
                        "result_chars": len(serialized),
                        "article": article,
                    }
                except (TimeoutError, ValueError, httpx.HTTPError) as exc:
                    return {
                        "url": url,
                        "ok": False,
                        "elapsed_ms": round((time.perf_counter() - start) * 1000),
                        "error": str(exc) or type(exc).__name__,
                        "error_type": type(exc).__name__,
                    }

        measurements = await asyncio.gather(*(measure(url) for url in sorted(urls)))
    deduplicated = deduplicate_articles(measurements)
    duplicates = [
        item["article"]["duplicate_sources"]
        for item in deduplicated
        if item.get("article", {}).get("duplicate_sources")
    ]
    for item in measurements:
        article = item.pop("article", {})
        item["title"] = article.get("title")
        item["text_chars"] = len(article.get("text") or "")
    report = {
        "measured_at": datetime.now(UTC).isoformat(),
        "search_ms": search_ms,
        "url_count": len(urls),
        "duplicate_groups": duplicates,
        "measurements": measurements,
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "urls": len(urls),
                "usable": sum(item["ok"] for item in measurements),
                "duplicate_groups": len(duplicates),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(audit(args.log, args.output))
