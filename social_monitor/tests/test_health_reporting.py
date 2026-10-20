import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_evidence import NOW, post

from social_monitor.cli import main as cli_main
from social_monitor.collector import Collection
from social_monitor.config import Config, Target
from social_monitor.health import collection_status, main, research_status
from social_monitor.reporting import coverage, review
from social_monitor.store import Store


class HealthReportingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = Target("one", "user", "example")
        self.config = Config(self.root, (self.target,), poll_seconds=60)

    def test_unstarted_probe_does_not_create_state(self):
        self.assertFalse(collection_status(self.config, NOW)["healthy"])
        self.assertFalse((self.root / "monitor.sqlite").exists())

    def test_one_empty_poll_is_degraded_but_sustained_failure_is_unhealthy(self):
        with closing(Store(self.root / "monitor.sqlite")) as store:
            store.complete_poll(self.target.key, NOW, NOW, [post()], {}, "ok")
            later = NOW + timedelta(seconds=60)
            store.complete_poll(self.target.key, later, later, [], {}, "failed", "empty_unverified")
            health = collection_status(self.config, later)
            self.assertTrue(health["healthy"])
            self.assertTrue(health["degraded"])
            self.assertFalse(health["coverage_verified"])
            self.assertFalse(collection_status(self.config, NOW + timedelta(seconds=196))["healthy"])
            store.complete_poll(self.target.key, later, later, [], {}, "failed", "access_blocked")
            self.assertFalse(collection_status(self.config, later)["healthy"])
            store.complete_poll(self.target.key, later, later, [], {}, "failed", "account_identity_mismatch")
            self.assertFalse(collection_status(self.config, later)["healthy"])

    def test_future_clock_and_unpolled_target_fail_health(self):
        with closing(Store(self.root / "monitor.sqlite")) as store:
            store.complete_poll(self.target.key, NOW, NOW, [post()], {}, "ok")
            self.assertFalse(collection_status(self.config, NOW - timedelta(seconds=1))["healthy"])
            config = Config(self.root, (self.target, Target("two", "user", "other")))
            self.assertFalse(collection_status(config, NOW)["healthy"])

    def test_review_changes_classification_without_mutating_history(self):
        path = self.root / "monitor.sqlite"
        old = {"version": 1, "relevance": "uncertain", "study_eligible": True}
        with closing(Store(path)) as store:
            item = post(text="Qubic attack drains wallets")
            store.complete_poll(self.target.key, NOW, NOW, [item], {}, "ok", assessments={item.id: old})
        with closing(Store(path, read_only=True)) as store:
            rows = list(review(store, NOW, limit=5, label="uncertain"))
            self.assertEqual(rows[0]["current_assessment"]["relevance"], "crypto")
            self.assertTrue(rows[0]["relevance_changed"])
            self.assertEqual(json.loads(store.connection.execute("SELECT payload FROM assessments").fetchone()[0]), old)
            self.assertEqual(store.status()["pending_alerts"], 0)

    def test_coverage_exposes_failures_and_quiet_timeline_without_claiming_misses(self):
        with closing(Store(self.root / "monitor.sqlite")) as store:
            old = post(age=2 * 86400)
            store.complete_poll(self.target.key, NOW, NOW, [old], {}, "ok")
            later = NOW + timedelta(seconds=30)
            store.complete_poll(self.target.key, later, later, [], {}, "failed", "empty_unverified")
            report = coverage(store, self.config, NOW, later)["targets"][0]
            self.assertEqual(report["ok_percent"], 50)
            self.assertTrue(report["independent_check_suggested"])
            self.assertFalse(report["coverage_verified"])
            self.assertEqual(report["errors"], {"empty_unverified": 1})

    def test_verification_reads_original_endpoint_without_ingesting_posts(self):
        config = self.root / "config.toml"
        config.write_text(
            'state_dir="state"\n[[targets]]\nname="one"\nkind="user"\nvalue="example"\n', encoding="utf-8"
        )
        state = self.root / "state"
        with closing(Store(state / "monitor.sqlite")) as store:
            store.complete_poll(self.target.key, NOW, NOW, [post()], {}, "ok")
        (state / "accounts.sqlite").touch()
        with patch("social_monitor.cli.XCollector") as factory, redirect_stdout(io.StringIO()) as output:
            factory.return_value.fetch = AsyncMock(return_value=Collection([post(), post("124")]))
            self.assertEqual(cli_main(["--config", str(config), "verify", "--target", "one"]), 0)
            self.assertFalse(factory.return_value.fetch.call_args.args[0].include_replies)
        result = json.loads(output.getvalue())
        self.assertEqual([row["already_stored"] for row in result["posts"]], [True, False])
        self.assertFalse(result["coverage_verified"])
        with closing(Store(state / "monitor.sqlite", read_only=True)) as store:
            self.assertEqual(store.status()["posts"], 1)
            self.assertEqual(store.status()["pending_alerts"], 0)

    def test_research_probe_rejects_stale_overdue_and_failed_jobs(self):
        path = self.root / "market.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                "CREATE TABLE heartbeat(id INTEGER PRIMARY KEY,at REAL); CREATE TABLE jobs(status TEXT,due REAL);"
            )
            db.execute("INSERT INTO heartbeat VALUES (1,?)", (NOW.timestamp(),))
            db.commit()
            self.assertTrue(research_status(path, NOW.timestamp())["healthy"])
            self.assertFalse(research_status(path, NOW.timestamp() + 301)["healthy"])
            db.execute("INSERT INTO jobs VALUES ('pending',?)", (NOW.timestamp() - 301,))
        self.assertFalse(research_status(path, NOW.timestamp())["healthy"])
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE jobs SET status='failed'")
        self.assertFalse(research_status(path, NOW.timestamp())["healthy"])

    def test_probe_imports_no_network_clients_or_event_loop_and_hides_io_paths(self):
        command = (
            "import social_monitor.health,sys;assert 'asyncio' not in sys.modules;"
            "assert 'urllib.request' not in sys.modules;assert 'dataclasses' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", command], check=True, capture_output=True)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(self.root / "secret-path"), "collector"]), 2)
        self.assertNotIn("secret-path", output.getvalue())

    @unittest.skipUnless(os.name == "posix", "Windows terminate does not deliver POSIX SIGTERM")
    def test_module_sigterm_unwinds_async_cleanup(self):
        script = """
import asyncio,runpy
from social_monitor import cli
async def work():
    try:
        print("ready",flush=True)
        await asyncio.sleep(60)
    finally:
        print("cleanup",flush=True)
def main():
    try:
        asyncio.run(work())
    except KeyboardInterrupt:
        return 130
cli.main=main
runpy.run_module("social_monitor",run_name="__main__")
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            process.send_signal(signal.SIGTERM)
            output, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130, errors)
            self.assertIn("cleanup", output)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
