"""Read bounded sanitized exports, never collector credentials or account databases."""

import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def discussion_evidence(topic: str, platforms: list[str], now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    directory = os.getenv("NEWS_SOCIAL_EXPORT_DIR")
    posts, errors = [], []
    if not directory:
        return {"posts": [], "errors": [{"platform": "collectors", "error": "export_not_configured"}]}
    for platform in sorted(set(platforms) & {"x", "reddit"}):
        path = Path(directory) / f"{platform}.jsonl"
        try:
            # A concurrent atomic export may complete just after this read's clock capture.
            if not -5 <= now.timestamp() - path.stat().st_mtime <= 300:
                raise ValueError("export_stale")
            if path.stat().st_size > 8000000:
                raise ValueError("export_too_large")
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= 1000:
                        break
                    item = json.loads(line)
                    published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
                    url = urlsplit(item["url"])
                    domains = {"x.com", "twitter.com"} if platform == "x" else {"reddit.com", "www.reddit.com"}
                    if url.scheme != "https" or url.hostname not in domains or url.username or url.password:
                        continue
                    if not now - timedelta(hours=48) <= published <= now:
                        continue
                    text = item["text"][:4000]
                    terms = set(topic.casefold().split()) - {"news", "latest", "market", "and", "the"}
                    if terms and not any(term in text.casefold() for term in terms):
                        continue
                    posts.append(
                        {
                            "evidence_id": hashlib.sha256((item["url"] + text).encode()).hexdigest()[:20],
                            "platform": platform,
                            "url": item["url"],
                            "title": text[:150],
                            "excerpt": text,
                            "published_at": item["published_at"],
                            "retrieved_at": item.get("received_at"),
                            "author": item.get("author"),
                            "access": "collector_observation",
                            "note": "Sampled public discussion; not a verified event or population sentiment",
                        }
                    )
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Social export unavailable platform=%s", platform)
            errors.append({"platform": platform, "error": "export_unavailable_or_invalid"})
    return {"posts": sorted(posts, key=lambda item: item["published_at"], reverse=True)[:30], "errors": errors}
