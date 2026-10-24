"""Exercise all public news tool entry points without model calls or database writes."""

import argparse
import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agno.run.agent import RunOutput

from news_agent.budget import ResearchBudget
from news_agent.config import NewsAgentSettings
from news_agent.coverage import collection_coverage
from news_agent.intelligence import evidence_documents
from news_agent.pipeline import research_trace
from news_agent.research import prepare_research
from news_agent.sources import PublicSourceTools
from news_agent.tools import NewsResearchTools


async def probe(asset: str) -> dict:
    settings = replace(NewsAgentSettings.load(), openrouter_api_key=None, database_url=None)
    budget = ResearchBudget(limits={"web_search": 20, "read_news_article": 16})
    public = PublicSourceTools(settings, budget)
    reader = NewsResearchTools(settings, budget, source_index=public.source_index)
    executions = []
    await prepare_research([public, public.search, reader], asset, executions)
    documents = evidence_documents(executions)
    cached = next(((url, value["article"]["text"]) for url, value in reader.cache.items() if value.get("ok")), None)
    reads_before = budget.calls["read_news_article"]
    read = json.loads(await reader.read_news_article(cached[0], find=cached[1][:30])) if cached else {}
    news = json.loads(await public.search.search_news(f"{asset} latest regulation", max_results=3))
    web = json.loads(await public.search.web_search(f"{asset} official announcement", max_results=3))
    discussion = json.loads(await public.search_public_discussion(f"{asset} news", ["x", "reddit"]))
    exchanges = json.loads(await public.search_exchange_announcements(asset, ["binance", "delta"]))
    return {
        "asset": asset,
        "checked_at": datetime.now(UTC).isoformat(),
        "model_calls": 0,
        "coverage": collection_coverage(executions),
        "trace": research_trace(RunOutput(tools=executions)),
        "documents": [
            {key: doc.get(key) for key in ("url", "kind", "title", "published_at", "language")}
            for doc in documents.values()
        ],
        "originals": sum(doc["kind"] == "article" for doc in documents.values()),
        "targeted_read_ok": bool(read.get("ok")),
        "targeted_read_reused_download": bool(cached) and budget.calls["read_news_article"] == reads_before,
        "news_search": news,
        "web_search": web,
        "discussion": discussion,
        "exchanges": exchanges,
        "budget": budget.snapshot(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True, choices=["BTC", "ETH"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(probe(args.asset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("asset", "originals", "targeted_read_ok", "targeted_read_reused_download", "model_calls")
            }
        )
    )
    return 0 if result["originals"] and result["targeted_read_ok"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
