"""Local coverage diagnostics and versioned screening comparisons, without network I/O."""

import json
from collections import Counter
from datetime import datetime

from .config import Config
from .domain import Post
from .relevance import VERSION, classify
from .store import Store


def coverage(store: Store, config: Config, since: datetime, now: datetime) -> dict:
    targets = []
    for target in config.targets:
        polls = store.connection.execute(
            "SELECT completed_at,status,error_code FROM polls WHERE target=? AND completed_at>=? ORDER BY id",
            (target.key, since.isoformat()),
        ).fetchall()
        successes = [datetime.fromisoformat(row["completed_at"]) for row in polls if row["status"] == "ok"]
        last = store.connection.execute(
            "SELECT completed_at FROM polls WHERE target=? AND status='ok' ORDER BY id DESC LIMIT 1",
            (target.key,),
        ).fetchone()
        newest = store.connection.execute(
            "SELECT max(p.published_at) FROM posts p JOIN target_posts t ON p.id=t.post_id WHERE t.target=?",
            (target.key,),
        ).fetchone()[0]
        age = (now - datetime.fromisoformat(newest)).total_seconds() if newest else None
        statuses = Counter(row["status"] for row in polls)
        gaps = [(b - a).total_seconds() for a, b in zip(successes, successes[1:], strict=False)]
        targets.append(
            {
                "target": target.key,
                "interval_seconds": config.interval(target),
                "attempts": len(polls),
                "statuses": dict(statuses),
                "ok_percent": round(statuses["ok"] / len(polls) * 100, 2) if polls else None,
                "errors": dict(Counter(row["error_code"] for row in polls if row["error_code"])),
                "last_success_at": last[0] if last else None,
                "current_success_gap_seconds": max(0, (now - datetime.fromisoformat(last[0])).total_seconds())
                if last
                else None,
                "max_completed_success_gap_seconds": max(gaps, default=None),
                "newest_published_at": newest,
                "newest_post_age_seconds": age,
                "independent_check_suggested": age is None or age > 86400,
                "coverage_verified": False,
            }
        )
    return {
        "since": since.isoformat(),
        "at": now.isoformat(),
        "targets": targets,
        "meaning": "Request success is not proof of complete coverage; old posts can reflect a quiet author",
    }


def review(store: Store, since: datetime, *, limit: int, label: str | None = None):
    rows = store.connection.execute(
        "SELECT p.post_json,p.first_seen_at,a.payload FROM posts p "
        "LEFT JOIN assessments a ON a.post_id=p.id WHERE p.first_seen_at>=? "
        "AND (? IS NULL OR json_extract(a.payload,'$.relevance')=?) "
        "ORDER BY p.first_seen_at DESC,p.id DESC LIMIT ?",
        (since.isoformat(), label, label, limit),
    )
    for row in rows:
        post = Post.from_dict(json.loads(row["post_json"]))
        original = json.loads(row["payload"]) if row["payload"] else None
        current = classify(post)
        yield {
            "post": post.to_dict(),
            "first_seen_at": row["first_seen_at"],
            "original_assessment": original,
            "current_assessment": current,
            "screening_version": VERSION,
            "relevance_changed": original is None or current["relevance"] != original["relevance"],
            "historical_evidence_modified": False,
        }
