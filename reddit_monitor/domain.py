"""Validate native evidence and screen relevance without inferring price direction."""

import math
import re
from datetime import UTC, datetime

FULLNAME = re.compile(r"t[1235]_[a-z0-9]+\Z")
SUBREDDIT = re.compile(r"[A-Za-z0-9_]{2,21}\Z")
TERMS = re.compile(
    r"\b(?:crypto(?:currency)?|bitcoin|ethereum|solana|dogecoin|memecoin|meme coin|blockchain|defi|airdrop|token)\b",
    re.I,
)
AMBIGUOUS = re.compile(r"\b(?:doge|pepe|moon|pump|shib|bonk)\b", re.I)
CASHTAG = re.compile(r"(?<!\w)\$([A-Za-z][A-Za-z0-9_]{0,14})\b")
ADDRESS = re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![A-Za-z0-9])")


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(UTC)


def normalize(child: dict, subreddit: str, received_at: str, provenance: dict) -> dict:
    received_at = timestamp(received_at).isoformat()
    if not isinstance(child, dict) or child.get("kind") not in {"t1", "t3"}:
        raise ValueError("unsupported object")
    raw = child.get("data")
    if not isinstance(raw, dict):
        raise ValueError("invalid object data")
    native = raw.get("name", "")
    if not isinstance(native, str) or not FULLNAME.fullmatch(native) or not native.startswith(child["kind"] + "_"):
        raise ValueError("invalid native ID")
    if raw.get("id") != native.split("_", 1)[1]:
        raise ValueError("inconsistent native ID")
    if str(raw.get("subreddit", "")).lower() != subreddit.lower():
        raise ValueError("subreddit mismatch")
    for field, prefix in (("subreddit_id", "t5_"), ("author_fullname", "t2_"), ("parent_id", None), ("link_id", "t3_")):
        value = raw.get(field)
        if value is not None and (
            not isinstance(value, str) or not FULLNAME.fullmatch(value) or (prefix and not value.startswith(prefix))
        ):
            raise ValueError("invalid context ID")
    seconds = raw.get("created_utc")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        raise ValueError("invalid publication time")
    published = datetime.fromtimestamp(seconds, UTC).isoformat()
    if (timestamp(published) - timestamp(received_at)).total_seconds() > 60:
        raise ValueError("publication time in future")
    permalink = raw.get("permalink")
    if not isinstance(permalink, str) or not re.fullmatch(r"/r/[A-Za-z0-9_]+/comments/[A-Za-z0-9_/-]+", permalink):
        raise ValueError("invalid canonical path")
    if permalink.split("/")[2].lower() != subreddit.lower():
        raise ValueError("canonical community mismatch")
    title, body = raw.get("title", ""), raw.get("body", raw.get("selftext", ""))
    if raw.get("author") is not None and (not isinstance(raw["author"], str) or len(raw["author"]) > 100):
        raise ValueError("invalid author")
    if not all(isinstance(s, str) and len(s) <= 100_000 for s in (title, body)):
        raise ValueError("invalid text")
    text = title + "\n" + body
    hits = sorted({m.group().lower() for m in TERMS.finditer(text)})
    candidates = sorted(set(CASHTAG.findall(text)) | set(ADDRESS.findall(text)))
    label = "relevant" if hits else "uncertain" if candidates or AMBIGUOUS.search(text) else "unrelated"
    deleted = body in {"[deleted]", "[removed]"} or raw.get("removed_by_category") is not None
    return {
        "schema_version": "social-evidence/1",
        "source": "reddit",
        "native_id": native,
        "source_id": "reddit:" + native,
        "kind": "comment" if child["kind"] == "t1" else "post",
        "event_at": published if raw.get("publication_time_verified", True) else None,
        "received_at": received_at,
        "source_updated_at": raw.get("feed_updated_at"),
        "event_time_kind": "publication" if raw.get("publication_time_verified", True) else "unknown",
        "canonical_url": "https://www.reddit.com" + permalink,
        "community": {"name": subreddit, "native_id": raw.get("subreddit_id")},
        "author": {"native_id": raw.get("author_fullname"), "name": raw.get("author")},
        "context": {
            "parent_id": raw.get("parent_id"),
            "thread_id": raw.get("link_id") or (native if child["kind"] == "t3" else None),
            "crosspost_ids": [p.get("name") for p in raw.get("crosspost_parent_list", []) if isinstance(p, dict)],
            "quote_ids": [],
            "context_complete": False,
        },
        "screening": {
            "version": "reddit-keywords/1",
            "label": label,
            "matched_terms": hits,
            "identity_candidates": candidates,
            "token_identity_verified": False,
            "market_direction": "unassessed",
        },
        "observation": {"edited": raw.get("edited", False), "deleted_or_removed": deleted},
        "provenance": provenance,
        "raw": raw,
    }
