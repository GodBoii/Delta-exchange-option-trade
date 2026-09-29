"""Additive SQLite evidence tables, isolated by asset and retained for 90 days."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .history import RETENTION_MS

TABLES = frozenset({"minute_candles", "option_observations", "futures_observations", "liquidity_summaries"})


class EvidenceStore:
    def __init__(self, path: str, asset: str) -> None:
        self.path, self.asset = Path(path), asset

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            for table in TABLES:
                db.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    asset TEXT NOT NULL, instrument TEXT NOT NULL, end INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(asset, instrument, end))""")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_time ON {table}(asset, end)")
            db.execute("""CREATE TABLE IF NOT EXISTS watched_expiries (
                asset TEXT NOT NULL, expiry INTEGER NOT NULL, PRIMARY KEY(asset, expiry))""")

    def save(self, table: str, end: int, rows: list[tuple[str, dict[str, Any]]]) -> None:
        if table not in TABLES:
            raise ValueError("Unknown evidence table")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany(f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?, ?)", [
                (self.asset, key, end, json.dumps(row, separators=(",", ":"), allow_nan=False)) for key, row in rows
            ])

    def read(self, table: str, start: int, end: int, instrument: str | None = None) -> list[dict[str, Any]]:
        if table not in TABLES:
            raise ValueError("Unknown evidence table")
        query = f"SELECT payload FROM {table} WHERE asset=? AND end>? AND end<=?"
        params: tuple = (self.asset, start, end)
        if instrument is not None:
            query += " AND instrument=?"
            params += (instrument,)
        with closing(sqlite3.connect(self.path)) as db:
            return [json.loads(row[0]) for row in db.execute(query + " ORDER BY end", params)]

    def watch(self, expiries: list[int], now: int) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany("INSERT OR IGNORE INTO watched_expiries VALUES (?,?)", [
                (self.asset, expiry) for expiry in expiries if expiry > now
            ])
            db.execute("DELETE FROM watched_expiries WHERE asset=? AND expiry<=?", (self.asset, now))

    def watched(self, now: int) -> set[int]:
        with closing(sqlite3.connect(self.path)) as db:
            return {row[0] for row in db.execute(
                "SELECT expiry FROM watched_expiries WHERE asset=? AND expiry>?", (self.asset, now)
            )}

    def prune(self, now: int) -> None:
        for table in TABLES:
            while True:
                with closing(sqlite3.connect(self.path)) as db, db:
                    removed = db.execute(f"""DELETE FROM {table} WHERE rowid IN (
                        SELECT rowid FROM {table} WHERE asset=? AND end<? LIMIT 1000)""",
                        (self.asset, now - RETENTION_MS)).rowcount
                if removed < 1000:
                    break
        self.watch([], now)
