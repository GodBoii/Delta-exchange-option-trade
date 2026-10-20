"""Behavioral tests use real SQLite state and synthetic network responses."""

import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime
from email.message import Message
from pathlib import Path

from reddit_monitor.__main__ import main
from reddit_monitor.collector import CollectionError, NoRedirect, Page, RSSClient, collect, parse_atom
from reddit_monitor.domain import normalize
from reddit_monitor.storage import Store, writer_lock

NOW = "2026-10-20T10:00:00+00:00"
LATER = "2026-10-20T10:01:00+00:00"


def child(native="t3_abc", text="bitcoin", created="2026-10-20T09:59:00+00:00", kind="t3"):
    return {
        "kind": kind,
        "data": {
            "name": native,
            "id": native.split("_")[1],
            "subreddit": "solana",
            "subreddit_id": "t5_test",
            "author_fullname": "t2_user",
            "author": "test_user",
            "created_utc": datetime.fromisoformat(created).timestamp(),
            "permalink": "/r/solana/comments/abc/test/",
            "body": text,
            "parent_id": "t3_abc" if kind == "t1" else None,
            "link_id": "t3_abc" if kind == "t1" else None,
        },
    }


def page(children=None, after=None, receipt=NOW):
    return Page(children if children is not None else [child()], after, receipt, {"mode": "test"})


class Tape:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.cursors = []

    def fetch(self, sub, stream, after, size):
        self.cursors.append(after)
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def poll(self, client, at=NOW, limit=4, stream="posts"):
        return collect(self.store, client, "solana", stream, limit, 50, at)

    def test_restart_partial_baseline_then_new_events_and_dedup(self):
        self.assertEqual(
            self.poll(Tape(page(after="t3_abc"), CollectionError("transport_failed"))), "partial_transport_failed"
        )
        self.store.close()
        self.store = Store(self.path)
        client = Tape(page([child("t3_def")]))
        self.poll(client)
        self.assertEqual(client.cursors, ["t3_abc"])
        self.assertFalse(any(e["research_event"] for e in self.store.export()))
        self.poll(Tape(page([child("t3_new", created=LATER)], receipt=LATER)), LATER)
        self.assertEqual(sum(e["research_event"] for e in self.store.export()), 1)
        self.poll(Tape(page([child("t3_new", created=LATER)], receipt=LATER)), LATER)
        self.assertEqual(len(list(self.store.export())), 3)

    def test_page_limit_persists_and_stalled_cursor_does_not_advance(self):
        self.assertEqual(self.poll(Tape(page(after="t3_abc")), limit=1), "page_limit")
        self.assertEqual(self.poll(Tape(page(after="t3_abc"))), "failed_pagination_stalled")
        self.assertEqual(self.store.status()[0]["cursor"], "t3_abc")
        self.assertEqual(self.store.status()[0]["initialized"], 0)

    def test_partial_invalid_page_keeps_useful_items_and_retries_same_cursor(self):
        bad = child("t3_bad")
        bad["data"]["created_utc"] = "bad"
        self.assertEqual(self.poll(Tape(page([child(), bad], "t3_abc"))), "partial_invalid_items")
        self.assertEqual(len(list(self.store.export())), 1)
        self.assertIsNone(self.store.status()[0]["cursor"])

    def test_timestamps_and_context_are_preserved(self):
        data = child("t1_c", kind="t1")
        self.poll(Tape(page([data], receipt=LATER)), stream="comments")
        value = next(self.store.export())
        self.assertEqual(value["event_at"], "2026-10-20T09:59:00+00:00")
        self.assertEqual(value["received_at"], LATER)
        self.assertEqual(value["context"]["thread_id"], "t3_abc")
        self.assertEqual(value["source_id"], "reddit:t1_c")
        with self.assertRaises(ValueError):
            normalize(data, "solana", "2026-10-20T10:00:00", {})

    def test_revision_history_then_deletion_erases_old_content(self):
        self.poll(Tape(page()))
        self.poll(Tape(page([child(text="edited bitcoin")], receipt=LATER)), LATER)
        self.assertEqual(len(list(self.store.export())), 2)
        self.poll(Tape(page([child(text="[deleted]")], receipt=LATER)), LATER)
        values = list(self.store.export())
        self.assertEqual(len(values), 1)
        self.assertIsNone(values[0]["raw"])
        self.assertNotIn("bitcoin", json.dumps(values))
        self.assertTrue(self.store.known("t3_abc"))
        self.poll(Tape(page()), LATER)
        self.assertIsNone(next(self.store.export())["raw"])

    def test_deleted_account_removes_related_author_observations(self):
        self.poll(Tape(page([child(), child("t3_def")])))
        deleted = child()
        deleted["data"]["author"] = "[deleted]"
        deleted["data"]["author_fullname"] = None
        self.poll(Tape(page([deleted], receipt=LATER)), LATER)
        self.assertNotIn("test_user", json.dumps(list(self.store.export())))

    def test_deletion_erases_embedded_crosspost_content(self):
        copied = child("t3_copy")
        copied["data"]["crosspost_parent_list"] = [child()["data"]]
        self.poll(Tape(page([child(), copied])))
        self.store.erase("t3_abc")
        self.assertEqual(list(self.store.export()), [])
        self.poll(Tape(page([copied])))
        self.assertEqual(list(self.store.export()), [])

    def test_retention_keeps_dedup_and_mode_isolation(self):
        self.poll(Tape(page()))
        self.store.purge_before(LATER)
        self.assertEqual(list(self.store.export()), [])
        self.assertTrue(self.store.known("t3_abc"))
        self.store.claim_mode("offline")
        with self.assertRaises(ValueError):
            self.store.claim_mode("approved_rss_research")

    def test_shared_quota_cooldown_survives_restart(self):
        reset = datetime.fromisoformat(LATER).timestamp() + 100
        self.poll(Tape(CollectionError("quota_cooldown", reset)))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.poll(Tape(), stream="comments"), "cooldown")

    def test_access_denial_stops_and_empty_is_unverified(self):
        with self.assertRaises(CollectionError):
            self.poll(Tape(CollectionError("access_denied")))
        self.assertEqual(self.store.status()[0]["status"], "failed_access_denied")
        self.assertEqual(self.poll(Tape(page([]))), "empty_unverified")

    def test_controls_and_unverified_identity(self):
        labels = [
            normalize(child(text=s), "solana", NOW, {})["screening"]
            for s in ("bitcoin crash", "pepe is funny $ABC", "garden flowers")
        ]
        self.assertEqual([s["label"] for s in labels], ["relevant", "uncertain", "unrelated"])
        self.assertTrue(all(s["market_direction"] == "unassessed" and not s["token_identity_verified"] for s in labels))

    def test_os_lock_conflict_and_release(self):
        with writer_lock(self.path), self.assertRaises(ValueError), writer_lock(self.path):
            self.fail("second writer acquired lock")
        with writer_lock(self.path):
            pass

    def test_readonly_status_and_export_work_while_writer_lock_is_held(self):
        self.poll(Tape(page()))
        self.store.claim_mode("offline")
        with writer_lock(self.path):
            reader = Store(self.path, readonly=True)
            try:
                reader.claim_mode("offline")
                self.assertEqual(len(list(reader.export())), 1)
                self.assertEqual(reader.status()[0]["status"], "bounded_read")
                self.assertEqual(list(reader.export(LATER)), [])
            finally:
                reader.close()

    def test_safe_errors_and_bounded_retries(self):
        class Opener:
            calls = 0

            def open(self, req, timeout):
                self.calls += 1
                raise urllib.error.URLError("secret-cookie-password")

        opener = Opener()
        client = RSSClient("test approval", "windows:reddit-monitor:v1 by test", opener=opener, sleep=lambda _: None)
        with self.assertRaisesRegex(CollectionError, "^transport_failed$"):
            client.fetch("solana", "posts", None, 50)
        self.assertEqual(opener.calls, 3)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(main(["run", "--once"]), 2)
        self.assertNotIn("secret", output.getvalue())

    def test_atom_unknown_fields_and_missing_publication_are_honest(self):
        xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>t3_abc</id>
        <updated>2026-10-20T09:59:00Z</updated><title>bitcoin</title>
        <link href="https://www.reddit.com/r/solana/comments/abc/test/"/>
        <content type="html">hello</content></entry></feed>"""
        result = parse_atom(xml, "solana", NOW, "/r/solana/new/.rss")
        self.assertTrue(result.window_only)
        self.assertFalse(result.children[0]["data"]["publication_time_verified"])
        self.assertIsNone(result.children[0]["data"]["author_fullname"])
        evidence = normalize(result.children[0], "solana", NOW, result.provenance)
        self.assertIsNone(evidence["event_at"])
        self.assertEqual(evidence["source_updated_at"], "2026-10-20T09:59:00Z")
        with self.assertRaises(CollectionError):
            parse_atom(b"<!DOCTYPE feed><feed/>", "solana", NOW, "/r/solana/new/.rss")

    def test_quota_retry_after_and_access_errors_do_not_leak_bodies(self):
        class Opener:
            calls = 0

            def __init__(self, status):
                self.status = status

            def open(self, req, timeout):
                self.calls += 1
                headers = Message()
                headers["Retry-After"] = "600"
                raise urllib.error.HTTPError(req.full_url, self.status, "secret-response", headers, None)

        for status, expected in ((429, "quota_cooldown"), (403, "access_denied")):
            opener = Opener(status)
            client = RSSClient(
                "test approval",
                "windows:reddit-monitor:v1 by test",
                opener=opener,
                sleep=lambda _: None,
                clock=lambda: 1000,
            )
            with self.assertRaises(CollectionError) as captured:
                client.fetch("solana", "posts", None, 50)
            self.assertEqual(captured.exception.code, expected)
            if status == 429:
                self.assertEqual(captured.exception.cooldown_until, 1600)
            self.assertNotIn("secret", str(captured.exception))
            self.assertEqual(opener.calls, 1)

    def test_redirects_and_unapproved_clients_fail_closed(self):
        with self.assertRaises(ValueError):
            RSSClient("", "windows:reddit-monitor:v1 by test")
        with self.assertRaisesRegex(CollectionError, "redirect_refused"):
            NoRedirect().redirect_request(None, None, 302, "secret", {}, "https://other.example/")

    def test_invalid_later_receipt_retains_previous_page(self):
        result = self.poll(Tape(page(after="t3_abc"), page(receipt="not-a-date")))
        self.assertEqual(result, "partial_invalid_receipt_time")
        self.assertEqual(self.store.status()[0]["cursor"], "t3_abc")
        self.assertEqual(len(list(self.store.export())), 1)

    def test_wrong_community_and_future_time_rejected(self):
        wrong = child()
        wrong["data"]["permalink"] = "/r/other/comments/abc/test/"
        with self.assertRaises(ValueError):
            normalize(wrong, "solana", NOW, {})
        with self.assertRaises(ValueError):
            normalize(child(created="2026-10-20T11:00:00Z"), "solana", NOW, {})

    def test_post_context_and_synthetic_evidence_never_becomes_live_event(self):
        self.poll(Tape(page()))
        synthetic = Page([child("t3_future", created=LATER)], None, LATER, {"mode": "synthetic_replay"})
        self.poll(Tape(synthetic), LATER)
        values = list(self.store.export())
        self.assertEqual(values[0]["context"]["thread_id"], "t3_abc")
        self.assertFalse(any(value["research_event"] for value in values))


if __name__ == "__main__":
    unittest.main()
