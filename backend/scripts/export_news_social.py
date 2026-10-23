"""Export only public post fields from collector SQLite stores to a separate shared volume."""

import json
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def export_rows(path: Path, platform: str) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as connection:
        connection.execute("pragma query_only=on")
        if platform == "x":
            rows = connection.execute(
                "select post_json,first_seen_at from posts where published_at >= ? "
                "order by published_at desc limit 1000",
                (cutoff,),
            ).fetchall()
            return [
                {
                    "text": value["text"][:4000],
                    "url": value["url"],
                    "published_at": value["published_at"],
                    "received_at": receipt,
                    "author": value.get("author"),
                }
                for raw, receipt in rows
                if isinstance(value := json.loads(raw), dict)
            ]
        rows = connection.execute(
            "select envelope from evidence where received_at >= ? order by received_at desc limit 1000",
            (cutoff,),
        ).fetchall()
    # Only the latest retained revision per source ID. Erased material is not persisted downstream.
    result = {}
    for (raw,) in rows:
        item = json.loads(raw)
        key = item.get("source_id")
        if key in result:
            continue
        observation = item.get("observation") or {}
        original = item.get("raw") or {}
        result[key] = None
        if (item.get("provenance") or {}).get("mode") != "rss":
            continue
        if observation.get("deleted_or_removed") or not item.get("canonical_url") or not item.get("event_at"):
            continue
        text = "\n".join(str(original.get(k) or "") for k in ("title", "selftext", "body", "content"))
        result[key] = {
            "text": text[:4000],
            "url": item["canonical_url"],
            "published_at": item["event_at"],
            "received_at": item["received_at"],
            "author": (item.get("author") or {}).get("name"),
        }
    return [value for value in result.values() if value]


def main() -> None:
    output = Path(os.environ["NEWS_SOCIAL_EXPORT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    while True:
        for platform, variable in (("x", "NEWS_X_DATABASE"), ("reddit", "NEWS_REDDIT_DATABASE")):
            try:
                if not os.getenv(variable):
                    continue
                rows = export_rows(Path(os.environ[variable]), platform)
                temporary = output / f"{platform}.pending"
                temporary.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
                os.chmod(temporary, 0o644)
                os.replace(temporary, output / f"{platform}.jsonl")
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                logger.exception("Public evidence export failed platform=%s", platform)
        time.sleep(60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
