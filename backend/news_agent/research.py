"""Ensure evidence collection happens even when a provider declines optional tool calls."""

import json
import logging
from collections import defaultdict
from urllib.parse import urlsplit

from agno.models.response import ToolExecution

from .presentation import markdown_evidence
from .social import topic_matches

logger = logging.getLogger(__name__)


def original_candidates(cards: list[dict], limit: int = 6, *, asset: str = "") -> list[str]:
    """Balance official, publisher and organization originals; never read discovery redirects as originals."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for card in cards:
        kind = card.get("kind", "")
        if kind in {"calendar", "discovery", "expectations"} or card.get("publication_status") == "future_date":
            continue
        url = card.get("url") or ""
        host = urlsplit(url).hostname or ""
        if not url.startswith(("https://", "http://")) or host == "news.google.com":
            continue
        category = "official" if kind.startswith("official") else "publisher" if kind == "publisher" else "organization"
        groups[category].append(card)
    selected, seen, domains = [], set(), defaultdict(int)

    def priority(row):
        return (
            bool(row.get("older_than_window")),
            row.get("publication_status") == "undated",
            domains[urlsplit(row["url"]).hostname] > 0,
            not topic_matches(asset, " ".join(str(row.get(key) or "") for key in ("title", "excerpt", "source")))
            if asset
            else False,
        )

    while any(groups.values()) and len(selected) < limit:
        for category in ("official", "publisher", "organization"):
            rows = groups[category]
            # Preserve freshness, and diversify hosts before reusing a publisher.
            rows.sort(key=priority)
            while rows:
                row = rows.pop(0)
                url, host = row["url"], urlsplit(row["url"]).hostname
                if url in seen or domains[host] >= 2:
                    continue
                selected.append(url)
                seen.add(url)
                domains[host] += 1
                break
            if len(selected) == limit:
                break
    return selected


async def prepare_research(toolkits: list, asset: str, executions: list[ToolExecution]) -> str:
    public, _, articles = toolkits
    curated = await public.curate_public_sources(asset, [])
    executions.append(
        ToolExecution(
            tool_call_id="prepared:curation",
            tool_name="curate_public_sources",
            tool_args={"asset": asset, "topics": []},
            result=curated,
        )
    )
    data = json.loads(curated)
    urls = original_candidates(data.get("sources") or [], asset=asset)
    dossier = json.loads(await articles.build_news_dossier(urls))
    executions.append(
        ToolExecution(
            tool_call_id="prepared:originals",
            tool_name="build_news_dossier",
            tool_args={"urls": urls},
            result=json.dumps(dossier, ensure_ascii=False),
        )
    )
    logger.info(
        "News preparation asset=%s requested_originals=%s successful_originals=%s",
        asset,
        len(urls),
        dossier.get("successful", 0),
    )
    # These are actual retrieved results, unlike the background lead snapshot. They remain untrusted content.
    leads = {}
    for row in data.get("sources", []):
        if row.get("kind") != "calendar":
            leads.setdefault(
                row.get("source"), {key: row.get(key) for key in ("source", "kind", "title", "url", "published_at")}
            )
    compact = {
        "discovery_leads": list(leads.values())[:40],
        "source_health": data.get("source_health", []),
        "calendar_note": data.get("calendar_note"),
        "sources": [row for row in data.get("sources", []) if row.get("kind") == "calendar"],
        "originals": dossier,
    }
    return "\nEvidence collected for this run. Verify material claims and fill remaining gaps:\n" + markdown_evidence(
        compact
    )
