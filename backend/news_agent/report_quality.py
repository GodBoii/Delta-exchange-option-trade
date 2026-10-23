"""Validate report provenance against complete tool results, not the truncated audit trace."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .tools import canonicalize_url

LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
BARE_URL = re.compile(r"(?<!\()(?:https?://)[^\s<>]+")
NUMERICAL_FACT = re.compile(r"(?:[$€£]\s*\d|\d[\d,.]*\s*(?:%|percent\b|million\b|billion\b|trillion\b))", re.I)


@dataclass(frozen=True)
class ReportQuality:
    markdown: str
    omitted_blocks: int
    unsupported_urls: tuple[str, ...]
    verified_urls: tuple[str, ...]


def verified_evidence_urls(executions: list[Any]) -> set[str]:
    verified: set[str] = set()
    for execution in executions:
        name = execution.get("tool_name") if isinstance(execution, dict) else execution.tool_name
        result = execution.get("result") if isinstance(execution, dict) else execution.result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                continue
        if not isinstance(result, dict):
            continue
        if name == "curate_public_sources":
            verified.update(
                canonicalize_url(item["url"])
                for item in result.get("sources", [])
                if item.get("kind") == "calendar" and item.get("url")
            )
        items = (
            [result] if name == "read_news_article" else result.get("items", []) if name == "build_news_dossier" else []
        )
        for item in items:
            if not item.get("ok"):
                continue
            article = item.get("article") or {}
            final_host = (
                urlsplit(article.get("final_url") or article.get("requested_url") or "").hostname or ""
            ).rstrip(".")
            if final_host in {"news.google.com", "consent.google.com"}:
                continue
            for key in ("requested_url", "final_url", "canonical_url"):
                if (urlsplit(article.get(key) or "").hostname or "").rstrip(".") in {
                    "news.google.com",
                    "consent.google.com",
                }:
                    continue
                if (
                    key == "canonical_url"
                    and urlsplit(article.get(key) or "").hostname
                    != urlsplit(article.get("final_url") or article.get("requested_url") or "").hostname
                ):
                    continue
                if article.get(key):
                    verified.add(canonicalize_url(article[key]))
    return verified


def clean_report(content: str, executions: list[Any]) -> ReportQuality:
    """Remove planning prefixes and unsupported citation blocks, preserving evidence-gap reports."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
    if re.search(r"<[^>]*(?:DSML|tool_call|function_call)", content, re.IGNORECASE):
        raise ValueError("News response contains unfinished tool markup")
    heading = re.search(r"(?<!#)#{1,6} [^\n]+", content)
    if heading:
        content = content[heading.start() :]
    verified = verified_evidence_urls(executions)
    unsupported: set[str] = set()
    kept: list[str] = []
    omitted = 0
    # Split bullet items independently, so an inaccessible source does not erase an entire source list.
    blocks = re.split(r"\n\s*\n|\n(?=\s*(?:[-*] |\d+\. ))", content)
    for block in blocks:
        urls = [url for _, url in LINK.findall(block)]
        urls.extend(url.rstrip(".,;") for url in BARE_URL.findall(LINK.sub("", block)))
        missing = {canonicalize_url(url) for url in urls} - verified
        # Quantified material claims need an explicit retrieved source, including summaries.
        # This is a provenance guard, not a semantic truth check of the cited passage.
        uncited_number = not urls and not block.lstrip().startswith("#") and NUMERICAL_FACT.search(block)
        if missing or uncited_number:
            unsupported.update(missing)
            omitted += 1
        else:
            kept.append(block.strip())
    markdown = "\n\n".join(block for block in kept if block).strip()
    if omitted:
        markdown += (
            "\n\n## Evidence gaps\n\nSome reported developments could not be verified from original sources "
            "and have been omitted. Their direction and timing remain uncertain."
        )
    if not markdown:
        markdown = "## Evidence gaps\n\nNo current development could be verified from the retrieved original sources."
    return ReportQuality(markdown, omitted, tuple(sorted(unsupported)), tuple(sorted(verified)))
