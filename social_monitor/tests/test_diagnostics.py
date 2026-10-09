import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_evidence import NOW, post

from social_monitor.diagnostics import warning_code
from social_monitor.domain import make_alert
from social_monitor.store import Store


class DiagnosticsTests(unittest.TestCase):
    def test_warning_codes_do_not_contain_message_or_credentials(self):
        cases = [
            ("twscrape.models", "Unknown card type auth_token=secret", "unsupported_card"),
            ("twscrape.models", "Failed to parse tweet secret", "parser_error"),
            ("twscrape.api", "UserTweets pagination stalled, stopping", "pagination_stalled"),
            ("twscrape.queue_client", "Session expired or banned: auth_token=secret", "authentication_required"),
            ("twscrape.queue_client", "Blocked by Cloudflare: 403 secret", "access_blocked"),
            ("twscrape.queue_client", "API busy: (-1) LoadShed", "upstream_busy"),
            ("twscrape.logger", "API busy: (-1) LoadShed", "upstream_busy"),
            ("twscrape.logger", "API unknown error: (89) Invalid or expired token secret", "authentication_required"),
        ]
        for module, message, code in cases:
            self.assertEqual(warning_code({"name": module, "message": message}), code)

    def test_old_database_upgrades_without_losing_posts_or_alerts(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE posts(id TEXT PRIMARY KEY, published_at TEXT, first_seen_at TEXT, post_json TEXT);
                CREATE TABLE alerts(id INTEGER PRIMARY KEY,post_id TEXT UNIQUE,payload TEXT,delivered_at TEXT);
                CREATE TABLE polls(id INTEGER PRIMARY KEY,target TEXT,started_at TEXT,completed_at TEXT,
                                   status TEXT,fetched INTEGER,inserted INTEGER,error_code TEXT);
                CREATE TABLE baselines(target TEXT PRIMARY KEY);
            """)
            db.execute(
                "INSERT INTO posts VALUES (?, ?, ?, ?)",
                ("123", NOW.isoformat(), NOW.isoformat(), json.dumps(post().to_dict())),
            )
            db.execute("INSERT INTO baselines VALUES ('old')")
            db.execute(
                "INSERT INTO alerts(post_id,payload) VALUES (?, ?)", ("123", json.dumps(make_alert(post(), NOW, 900)))
            )
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store.status()["posts"], 1)
        self.assertTrue(store.has_baseline("old"))
        self.assertEqual(store.status()["pending_alerts"], 1)
        self.assertEqual(store.recent_ids("user:example:True", "example"), frozenset({"123"}))
        store.complete_poll("old", NOW, NOW, [post()], {}, "ok", details={"pages": 1})
        self.assertEqual(store.status()["targets"][0]["details"], {"pages": 1})
        self.assertEqual(store.statistics(NOW)["collections"][0]["new_posts"], 0)
