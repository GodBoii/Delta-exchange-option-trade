"""Own SQLite database, atomic page checkpoints, OS process lock and deletion handling."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .domain import timestamp


def native_objects(raw) -> set[str]:
    result = set()
    pending = [raw]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if isinstance(value.get("name"), str):
                result.add(value["name"])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return result


@contextmanager
def writer_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "writer.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("writer_locked") from None

            def unlock():
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("writer_locked") from None

            def unlock():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        try:
            yield
        finally:
            handle.seek(0)
            unlock()


class Store:
    def __init__(self, directory: Path, readonly: bool = False):
        self.readonly = readonly
        database = directory / "evidence.sqlite"
        if readonly:
            self.db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        if readonly:
            return
        self.db.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS targets (
                target TEXT PRIMARY KEY, baseline_at TEXT NOT NULL, initialized INTEGER NOT NULL DEFAULT 0,
                cursor TEXT, pending INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0,
                last_attempt TEXT, last_success TEXT, status TEXT NOT NULL DEFAULT 'never',
                cooldown_until REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS evidence (
                native_id TEXT NOT NULL, digest TEXT NOT NULL, received_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, envelope TEXT NOT NULL,
                PRIMARY KEY(native_id, digest));
            CREATE INDEX IF NOT EXISTS evidence_receipt ON evidence(received_at);
            CREATE TABLE IF NOT EXISTS seen (native_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tombstones (native_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY, target TEXT NOT NULL, received_at TEXT NOT NULL,
                cursor_in TEXT, cursor_out TEXT, count INTEGER NOT NULL, provenance TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def claim_mode(self, mode: str):
        if not self.readonly:
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('access_mode',?)", (mode,))
        if self.db.execute("SELECT value FROM metadata WHERE key='access_mode'").fetchone()[0] != mode:
            raise ValueError("state_access_mode_mismatch")

    def global_cooldown(self) -> float:
        row = self.db.execute("SELECT value FROM metadata WHERE key='cooldown'").fetchone()
        return float(row[0]) if row else 0

    def target(self, name: str, now: str) -> dict:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO targets(target, baseline_at) VALUES (?,?)", (name, now))
        return dict(self.db.execute("SELECT * FROM targets WHERE target=?", (name,)).fetchone())

    def begin(self, name: str, now: str) -> dict:
        state = self.target(name, now)
        with self.db:
            self.db.execute("UPDATE targets SET pending=1,last_attempt=?,status='running' WHERE target=?", (now, name))
        return state

    def known(self, native_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM seen WHERE native_id=?", (native_id,)).fetchone() is not None

    def save_page(
        self, name: str, envelopes: list[dict], now: str, before: str | None, after: str | None, provenance: dict
    ):
        state = self.target(name, now)
        with self.db:
            for envelope in envelopes:
                native = envelope["native_id"]
                object_ids = native_objects(envelope["raw"]) | {native}
                if any(
                    self.db.execute("SELECT 1 FROM tombstones WHERE native_id=?", (obj,)).fetchone()
                    for obj in object_ids
                ):
                    continue
                exists = self.known(native)
                event_at = envelope["event_at"]
                age = (timestamp(now) - timestamp(event_at)).total_seconds() if event_at else -1
                envelope["research_event"] = bool(
                    state["initialized"]
                    and not exists
                    and event_at
                    and timestamp(event_at) >= timestamp(state["baseline_at"])
                    and 0 <= age <= 3600
                    and envelope["raw"].get("publication_time_verified", True)
                    and envelope["provenance"].get("mode") != "synthetic_replay"
                )
                envelope["observation_class"] = (
                    "baseline" if not state["initialized"] else "revision" if exists else "discovery"
                )
                self.db.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (native, now))
                if envelope["author"]["name"] == "[deleted]":
                    previous = self.db.execute(
                        "SELECT envelope FROM evidence WHERE native_id=? LIMIT 1", (native,)
                    ).fetchone()
                    if previous:
                        author = json.loads(previous[0]).get("author")
                        if author:
                            self.erase_author(author.get("native_id"), author.get("name"))
                    envelope["author"] = None
                    envelope["raw"] = {**envelope["raw"], "author": None, "author_fullname": None}
                if envelope["observation"]["deleted_or_removed"]:
                    self.erase_content(native)
                    self.db.execute("INSERT OR IGNORE INTO tombstones VALUES (?)", (native,))
                    envelope["raw"] = None
                    envelope["screening"] = None
                    envelope["author"] = None
                    envelope["canonical_url"] = None
                    envelope["context"] = None
                    envelope["research_event"] = False
                digest = hashlib.sha256(json.dumps(envelope["raw"], sort_keys=True).encode()).hexdigest()
                self.db.execute(
                    """INSERT INTO evidence VALUES (?,?,?,?,?)
                    ON CONFLICT(native_id,digest) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                    (native, digest, now, now, json.dumps(envelope)),
                )
            self.db.execute(
                "INSERT INTO pages(target,received_at,cursor_in,cursor_out,count,provenance) VALUES (?,?,?,?,?,?)",
                (name, now, before, after, len(envelopes), json.dumps(provenance)),
            )
            self.db.execute("UPDATE targets SET cursor=?,pages=pages+1 WHERE target=?", (after, name))

    def finish(self, name: str, now: str, status: str, complete: bool, cooldown: float = 0):
        with self.db:
            if "quota_cooldown" in status:
                self.db.execute(
                    "INSERT INTO metadata VALUES ('cooldown',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(cooldown),),
                )
            if complete:
                self.db.execute(
                    """UPDATE targets SET initialized=1,pending=0,cursor=NULL,pages=0,
                    last_success=?,status=?,cooldown_until=? WHERE target=?""",
                    (now, status, cooldown, name),
                )
            else:
                self.db.execute("UPDATE targets SET status=?,cooldown_until=? WHERE target=?", (status, cooldown, name))

    def export(self, cutoff: str = ""):
        key = (cutoff, "", "")
        while True:
            # Release the read cursor between batches so slow stdout cannot block a writer.
            rows = self.db.execute(
                """SELECT received_at,native_id,digest,envelope,last_seen_at FROM evidence
                WHERE (received_at,native_id,digest) > (?,?,?)
                ORDER BY received_at,native_id,digest LIMIT 100""",
                key,
            ).fetchall()
            if not rows:
                return
            for row in rows:
                yield {**json.loads(row["envelope"]), "last_seen_at": row["last_seen_at"]}
            last = rows[-1]
            key = (last["received_at"], last["native_id"], last["digest"])

    def status(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM targets ORDER BY target")]

    def purge_before(self, cutoff: str):
        with self.db:
            self.db.execute("DELETE FROM evidence WHERE received_at < ?", (cutoff,))
            self.db.execute("DELETE FROM pages WHERE received_at < ?", (cutoff,))

    def erase(self, native_id: str):
        with self.db:
            self.erase_content(native_id)
            self.db.execute("INSERT OR IGNORE INTO tombstones VALUES (?)", (native_id,))

    def erase_content(self, native_id: str):
        # Include copied native objects embedded in a crosspost's raw payload.
        for row in self.db.execute("SELECT native_id,digest,envelope FROM evidence").fetchall():
            raw = json.loads(row["envelope"]).get("raw")
            matches = row["native_id"] == native_id or native_id in native_objects(raw)
            if matches:
                self.db.execute(
                    "DELETE FROM evidence WHERE native_id=? AND digest=?", (row["native_id"], row["digest"])
                )

    def erase_author(self, native_id: str | None, name: str | None):
        # Delete whole related observations, including embedded Atom/crosspost references.
        if not native_id and (not name or name == "[deleted]"):
            return
        for row in self.db.execute("SELECT native_id,digest,envelope FROM evidence").fetchall():
            value = row["envelope"]
            if (native_id and native_id in value) or (name and name in value):
                self.db.execute(
                    "DELETE FROM evidence WHERE native_id=? AND digest=?", (row["native_id"], row["digest"])
                )
