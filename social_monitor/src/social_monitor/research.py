"""Restart-safe event studies in a separate database and process from X collection."""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from .domain import parse_time
from .market import MINUTE, SYMBOLS, BinanceCandles, MarketError, baseline_open, measure

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), post_rowid INTEGER NOT NULL);
INSERT OR IGNORE INTO checkpoint VALUES (1, 0);
CREATE TABLE IF NOT EXISTS events (
    post_id TEXT PRIMARY KEY, published_at TEXT NOT NULL, received_at TEXT NOT NULL,
    assessment TEXT NOT NULL, url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    post_id TEXT NOT NULL REFERENCES events(post_id), horizon INTEGER NOT NULL,
    due REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','complete','failed')),
    result TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(post_id,horizon)
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,due);
CREATE TABLE IF NOT EXISTS heartbeat (id INTEGER PRIMARY KEY CHECK(id=1), at REAL NOT NULL);
"""


class Research:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        path.chmod(0o600)

    def close(self) -> None:
        self.connection.close()

    def discover(self, source: sqlite3.Connection) -> int:
        cursor = self.connection.execute("SELECT post_rowid FROM checkpoint WHERE id=1").fetchone()[0]
        rows = source.execute(
            "SELECT p.rowid, p.id, p.published_at, p.first_seen_at, p.post_json, a.payload "
            "FROM posts p LEFT JOIN assessments a ON a.post_id=p.id WHERE p.rowid > ? ORDER BY p.rowid LIMIT 500",
            (cursor,),
        ).fetchall()
        count = 0
        with self.connection:
            for rowid, post_id, published, received, post_json, payload in rows:
                assessment = json.loads(payload) if payload else {}
                if assessment.get("study_eligible"):
                    # Preserve unrelated/uncertain posts as market controls. No selection by future returns.
                    self.connection.execute(
                        "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?)",
                        (post_id, published, received, payload, json.loads(post_json)["url"]),
                    )
                    for horizon in (1, 5, 15, 60):
                        due = (
                            max(parse_time(published).timestamp(), parse_time(received).timestamp()) + horizon * 60 + 5
                        )
                        self.connection.execute(
                            "INSERT OR IGNORE INTO jobs(post_id,horizon,due) VALUES (?, ?, ?)", (post_id, horizon, due)
                        )
                    count += 1
                self.connection.execute("UPDATE checkpoint SET post_rowid=? WHERE id=1", (rowid,))
        return count

    def cycle(self, client: BinanceCandles, emit, now: datetime) -> int:
        jobs = self.connection.execute(
            "SELECT j.post_id,j.horizon,j.attempts,j.result,e.published_at,e.received_at,e.assessment,e.url "
            "FROM jobs j JOIN events e ON e.post_id=j.post_id WHERE j.status='pending' AND j.due <= ? "
            "ORDER BY j.due,j.post_id LIMIT 8",
            (now.timestamp(),),
        ).fetchall()
        completed = 0
        for job in jobs:
            assessment = json.loads(job["assessment"])
            published, received = parse_time(job["published_at"]), parse_time(job["received_at"])
            start = min(baseline_open(published), baseline_open(received)) - 5 * MINUTE
            end = max(baseline_open(published), baseline_open(received)) + job["horizon"] * MINUTE
            assets = sorted(set(assessment["assets"]) & SYMBOLS.keys() | {"BTC", "ETH"})
            result = json.loads(job["result"])
            markets = result.get("markets", {})
            errors = {}
            retry_seconds = 60
            rate_limited = False
            for asset in assets:
                if asset in markets:
                    continue
                try:
                    candles = client.fetch(asset, start, end)
                    markets[asset] = measure(candles, published, received, job["horizon"])
                except MarketError as error:
                    errors[asset] = error.code
                    retry_seconds = max(retry_seconds, error.retry_seconds)
                    if error.code == "market_rate_limited":
                        rate_limited = True
                        break
            for asset, data in markets.items():
                change = data["receipt_return_pct"]
                data["observed_direction"] = "up" if change > 0 else "down" if change < 0 else "flat"
                data["receipt_excess_vs_benchmark_pct_points"] = {
                    benchmark: round(data["receipt_return_pct"] - markets[benchmark]["receipt_return_pct"], 8)
                    for benchmark in ("BTC", "ETH")
                    if benchmark in markets and benchmark != asset
                }
            complete = len(markets) == len(assets)
            attempts = job["attempts"] + 1
            status = "complete" if complete else "failed" if attempts >= 8 else "pending"
            result = {
                "event": "price_response",
                "post_id": job["post_id"],
                "url": job["url"],
                "horizon_minutes": job["horizon"],
                "published_at": job["published_at"],
                "received_at": job["received_at"],
                "measured_at": now.isoformat(),
                "assessment": assessment,
                "status": status,
                "markets": markets,
                "errors": errors,
                "source": "binance_spot_public_1m",
                "mapping": "named_asset_price_proxy_not_contract_verification",
                "method": "last_closed_minute_at_each_anchor; benchmark_difference_not_beta_adjusted",
                "executable_return": False,
                "causal_effect_established": False,
                "unresolved_references": assessment.get("unresolved_cashtags", []),
            }
            with self.connection:
                self.connection.execute(
                    "UPDATE jobs SET attempts=?,status=?,result=?,due=? WHERE post_id=? AND horizon=?",
                    (
                        attempts,
                        status,
                        json.dumps(result),
                        now.timestamp() + max(retry_seconds, min(60 * 2**attempts, 3600)),
                        job["post_id"],
                        job["horizon"],
                    ),
                )
            emit(result)
            completed += complete
            if rate_limited:
                # A venue cooldown applies to every job, not only this post.
                with self.connection:
                    self.connection.execute(
                        "UPDATE jobs SET due=max(due,?) WHERE status='pending'", (now.timestamp() + retry_seconds,)
                    )
                break
        with self.connection:
            self.connection.execute(
                "INSERT INTO heartbeat VALUES (1,?) ON CONFLICT(id) DO UPDATE SET at=excluded.at", (now.timestamp(),)
            )
        return completed


def status(path: Path) -> dict:
    if not path.exists():
        return {"event": "research_not_started", "healthy": False}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        heartbeat = connection.execute("SELECT at FROM heartbeat WHERE id=1").fetchone()
        counts = dict(connection.execute("SELECT status,count(*) FROM jobs GROUP BY status"))
        oldest = connection.execute("SELECT min(due) FROM jobs WHERE status='pending'").fetchone()[0]
        now = time.time()
        return {
            "jobs": counts,
            "last_cycle_at": heartbeat[0] if heartbeat else None,
            "overdue_seconds": max(0, now - oldest) if oldest else 0,
            "healthy": bool(
                heartbeat
                and now - heartbeat[0] < 300
                and not counts.get("failed")
                and (oldest is None or now - oldest < 300)
            ),
        }
    finally:
        connection.close()


def run(source_path: Path, research: Research, emit, diagnostic, *, once: bool = False) -> bool:
    client = BinanceCandles()
    while True:
        if not source_path.exists():
            diagnostic({"event": "research_waiting_for_monitor"})
            if once:
                return False
        else:
            source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                ready = bool(source.execute("SELECT 1 FROM sqlite_master WHERE name='assessments'").fetchone())
                discovered = research.discover(source) if ready else 0
            finally:
                source.close()
            if not ready:
                diagnostic({"event": "research_waiting_for_monitor_v3"})
                if once:
                    return False
                time.sleep(30)
                continue
            completed = research.cycle(client, emit, datetime.now(UTC))
            diagnostic({"event": "research_cycle", "discovered": discovered, "completed": completed})
            if once:
                return not research.connection.execute(
                    "SELECT 1 FROM jobs WHERE attempts > 0 AND status != 'complete' LIMIT 1"
                ).fetchone()
        time.sleep(30)
