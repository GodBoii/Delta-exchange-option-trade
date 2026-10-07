"""Local evidence and durable stdout alert outbox, independent of trading storage."""

import json
import sqlite3
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

    def complete_poll(
        self,
        target: str,
        started: datetime,
        completed: datetime,
        posts: list[Post],
        alerts: dict[str, dict],
        status: str,
        error_code: str | None = None,
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
            self.connection.execute(
                "INSERT INTO polls(target,started_at,completed_at,status,fetched,inserted,error_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (target, started.isoformat(), completed.isoformat(), status, len(posts), inserted, error_code),
            )
            if status in {"ok", "empty"}:
                self.connection.execute("INSERT OR IGNORE INTO baselines VALUES (?)", (target,))
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
        rows = self.connection.execute(
            "SELECT target, started_at, completed_at, status, fetched, inserted, error_code FROM polls "
            "WHERE id IN (SELECT max(id) FROM polls GROUP BY target) ORDER BY target"
        )
        return {**counts, "targets": [dict(row) for row in rows]}

    def export(self, emit) -> None:
        for row in self.connection.execute("SELECT post_json, first_seen_at FROM posts ORDER BY first_seen_at, id"):
            emit({**json.loads(row["post_json"]), "first_seen_at": row["first_seen_at"]})
