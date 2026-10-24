"""Repeat public news tools and retain their actual JSON payloads, without model or database calls."""

import argparse
import asyncio
import json
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from news_agent.budget import ResearchBudget
from news_agent.config import NewsAgentSettings
from news_agent.research import original_candidates
from news_agent.sources import PublicSourceTools
from news_agent.tools import NewsResearchTools

TOPICS = (
    "Bitcoin ETF cryptocurrency markets",
    "Ethereum network upgrade security",
    "cryptocurrency regulation tax law digital assets",
    'government fiscal report GDP inflation "Fiscal Monitor"',
    "tariffs sanctions trade restrictions export bans",
    "war geopolitical conflict oil energy supply",
)


async def audit(rounds: int, pause: float) -> dict:
    settings = replace(NewsAgentSettings.load(), openrouter_api_key=None, database_url=None)
    observations = []

    async def observe(asset, round_id, repeat, name, arguments, function):
        started = time.monotonic()
        raw = await function(**arguments)
        payload = json.loads(raw)
        # Reject nonstandard NaN/Infinity or nonserializable payloads before retaining the observation.
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        rows = (
            payload
            if isinstance(payload, list)
            else next(
                (payload[key] for key in ("sources", "items", "posts", "announcements", "results") if key in payload),
                [],
            )
        )
        ok = payload.get("ok", True) if isinstance(payload, dict) else True
        observations.append(
            {
                "asset": asset,
                "round": round_id,
                "repeat": repeat,
                "tool": name,
                "arguments": arguments,
                "observed_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "bytes": len(encoded.encode()),
                "ok": ok,
                "row_count": len(rows),
                "payload": payload,
            }
        )
        return payload

    for index in range(rounds):
        for asset in ("BTC", "ETH"):
            budget = ResearchBudget(limits={"web_search": 20, "read_news_article": 16})
            public = PublicSourceTools(settings, budget)
            reader = NewsResearchTools(settings, budget, source_index=public.source_index)
            topic = TOPICS[(index * 2 + (asset == "ETH")) % len(TOPICS)]
            curated = await observe(
                asset, index, 0, "curate_public_sources", {"asset": asset, "topics": []}, public.curate_public_sources
            )
            urls = original_candidates(curated.get("sources", []), asset=asset)
            for repeat in range(2):
                if repeat:
                    await observe(
                        asset,
                        index,
                        repeat,
                        "curate_public_sources",
                        {"asset": asset, "topics": []},
                        public.curate_public_sources,
                    )
                dossier = await observe(
                    asset, index, repeat, "build_news_dossier", {"urls": urls}, reader.build_news_dossier
                )
                successful = next(
                    (x["article"]["requested_url"] for x in dossier.get("items", []) if x.get("ok")), None
                )
                if successful:
                    await observe(
                        asset, index, repeat, "read_news_article", {"url": successful}, reader.read_news_article
                    )
                await observe(
                    asset, index, repeat, "search_news", {"query": topic, "max_results": 5}, public.search.search_news
                )
                await observe(
                    asset, index, repeat, "web_search", {"query": topic, "max_results": 5}, public.search.web_search
                )
                await observe(
                    asset,
                    index,
                    repeat,
                    "search_public_discussion",
                    {"topic": f"{asset} news", "platforms": ["x", "reddit"]},
                    public.search_public_discussion,
                )
                await observe(
                    asset,
                    index,
                    repeat,
                    "search_exchange_announcements",
                    {"topic": asset, "exchanges": ["binance", "delta"]},
                    public.search_exchange_announcements,
                )
        if index + 1 < rounds:
            await asyncio.sleep(pause)
    return {"model_calls": 0, "rounds": rounds, "observations": observations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--pause", type=float, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(audit(args.rounds, max(5, min(args.pause, 60))))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"model_calls": 0, "observations": len(result["observations"]), "output": str(args.output)}))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
