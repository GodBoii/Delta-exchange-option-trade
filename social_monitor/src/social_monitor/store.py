"""Local evidence and durable stdout alert outbox, independent of trading storage."""

import json
import math
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

from .domain import Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    published_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    post_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    post_id TEXT NOT NULL UNIQUE REFERENCES posts(id),
    payload TEXT NOT NULL,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY,
    target TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ok','empty','failed','partial')),
    fetched INTEGER NOT NULL,
    inserted INTEGER NOT NULL,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS polls_target_id ON polls(target, id DESC);
CREATE TABLE IF NOT EXISTS baselines (target TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS poll_details (
    poll_id INTEGER PRIMARY KEY REFERENCES polls(id),
    details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS target_posts (
    target TEXT NOT NULL,
    post_id TEXT NOT NULL REFERENCES posts(id),
    PRIMARY KEY (target, post_id)
);
CREATE TABLE IF NOT EXISTS reconciliations (
    target TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS polls_completed_at ON polls(completed_at);
CREATE INDEX IF NOT EXISTS alerts_pending ON alerts(id) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS posts_first_seen ON posts(first_seen_at);
"""


class Store:
    def __init__(self, path: Path, *, read_only: bool = False):
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = (
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) if read_only else sqlite3.connect(path)
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.executescript(SCHEMA)
            path.chmod(0o600)

    def close(self) -> None:
        self.connection.close()

    def has_baseline(self, target: str) -> bool:
        return self.connection.execute("SELECT 1 FROM baselines WHERE target = ?", (target,)).fetchone() is not None

    def recent_ids(self, target: str, author: str | None = None, limit: int = 2000) -> frozenset[str]:
        rows = self.connection.execute(
            "SELECT post_id FROM target_posts WHERE target = ? ORDER BY length(post_id) DESC, post_id DESC LIMIT ?",
            (target, limit),
        ).fetchall()
        if not rows and author is not None:
            rows = self.connection.execute(
                "SELECT id FROM posts WHERE json_extract(post_json, '$.author') = ? COLLATE NOCASE "
                "ORDER BY first_seen_at DESC LIMIT ?",
                (author, limit),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def reconciliation_due(self, target: str, now: datetime, interval: float) -> bool:
        row = self.connection.execute("SELECT completed_at FROM reconciliations WHERE target = ?", (target,)).fetchone()
        return row is None or (now - datetime.fromisoformat(row[0])).total_seconds() >= interval

    def complete_poll(
        self,
        target: str,
        started: datetime,
        completed: datetime,
        posts: list[Post],
        alerts: dict[str, dict],
        status: str,
        error_code: str | None = None,
        *,
        details: dict | None = None,
        reconciled: bool = False,
    ) -> int:
        inserted = 0
        with self.connection:
            for post in posts:
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO posts VALUES (?, ?, ?, ?)",
                    (post.id, post.published_at, completed.isoformat(), json.dumps(post.to_dict())),
                )
                if cursor.rowcount:
                    inserted += 1
                    if post.id in alerts:
                        self.connection.execute(
                            "INSERT INTO alerts(post_id, payload) VALUES (?, ?)",
                            (post.id, json.dumps(alerts[post.id])),
                        )
                self.connection.execute("INSERT OR IGNORE INTO target_posts VALUES (?, ?)", (target, post.id))
            cursor = self.connection.execute(
                "INSERT INTO polls(target,started_at,completed_at,status,fetched,inserted,error_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (target, started.isoformat(), completed.isoformat(), status, len(posts), inserted, error_code),
            )
            if details is not None:
                self.connection.execute(
                    "INSERT INTO poll_details VALUES (?, ?)", (cursor.lastrowid, json.dumps(details))
                )
            if status in {"ok", "empty"}:
                self.connection.execute("INSERT OR IGNORE INTO baselines VALUES (?)", (target,))
            if reconciled and posts and status in {"ok", "partial"}:
                # A partially useful deep read must not force deep pagination on every fast poll.
                self.connection.execute(
                    "INSERT INTO reconciliations VALUES (?, ?) ON CONFLICT(target) "
                    "DO UPDATE SET completed_at = excluded.completed_at",
                    (target, completed.isoformat()),
                )
        return inserted

    def deliver(self, emit, delivered_at: datetime) -> int:
        count = 0
        while row := self.connection.execute(
            "SELECT id, payload FROM alerts WHERE delivered_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone():
            emit({"alert_id": row["id"], **json.loads(row["payload"])})
            with self.connection:
                self.connection.execute(
                    "UPDATE alerts SET delivered_at = ? WHERE id = ?", (delivered_at.isoformat(), row["id"])
                )
            count += 1
        return count

    def status(self) -> dict:
        counts = {
            "posts": self.connection.execute("SELECT count(*) FROM posts").fetchone()[0],
            "pending_alerts": self.connection.execute(
                "SELECT count(*) FROM alerts WHERE delivered_at IS NULL"
            ).fetchone()[0],
        }
        details_available = (
            self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='poll_details'").fetchone()
            is not None
        )
        rows = self.connection.execute(
            "SELECT id, target, started_at, completed_at, status, fetched, inserted, error_code FROM polls "
            "WHERE id IN (SELECT max(id) FROM polls GROUP BY target) ORDER BY target"
        ).fetchall()
        targets = []
        for row in rows:
            item = dict(row)
            if details_available:
                detail = self.connection.execute(
                    "SELECT details_json FROM poll_details WHERE poll_id = ?", (item["id"],)
                ).fetchone()
                item["details"] = json.loads(detail[0]) if detail else {}
            success = self.connection.execute(
                "SELECT completed_at FROM polls WHERE target = ? AND status IN ('ok','empty') ORDER BY id DESC LIMIT 1",
                (item["target"],),
            ).fetchone()
            item["last_success_at"] = success[0] if success else None
            item.pop("id")
            targets.append(item)
        return {**counts, "targets": targets}

    def statistics(self, since: datetime) -> dict:
        rows = self.connection.execute(
            "SELECT target, status, count(*) AS attempts, sum(inserted) AS new_posts "
            "FROM polls WHERE completed_at >= ? GROUP BY target, status ORDER BY target, status",
            (since.isoformat(),),
        )
        alerts = self.connection.execute(
            "SELECT a.payload FROM alerts a JOIN posts p ON p.id = a.post_id WHERE p.first_seen_at >= ?",
            (since.isoformat(),),
        )
        delays = sorted(json.loads(row[0])["publication_to_receipt_seconds"] for row in alerts)
        latency = (
            {
                "samples": len(delays),
                "median": statistics.median(delays),
                "p95": delays[math.ceil(len(delays) * 0.95) - 1],
                "max": max(delays),
            }
            if delays
            else {"samples": 0}
        )
        return {"since": since.isoformat(), "collections": [dict(row) for row in rows], "alert_delay_seconds": latency}

    def export(self, emit) -> None:
        for row in self.connection.execute("SELECT post_json, first_seen_at FROM posts ORDER BY first_seen_at, id"):
            emit({**json.loads(row["post_json"]), "first_seen_at": row["first_seen_at"]})
