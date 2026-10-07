import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from social_monitor.config import load_config
from social_monitor.domain import Post, make_alert, references
from social_monitor.store import Store

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def post(post_id="123", text="$DOGE", age=0):
    return Post.from_dict(
        {
            "id": post_id,
            "author_id": "456",
            "author": "example",
            "text": text,
            "published_at": (NOW - timedelta(seconds=age)).isoformat(),
        }
    )


class EvidenceTests(unittest.TestCase):
    def test_reference_candidates_are_not_token_identities(self):
        result = references("$doge $DOGE 0x" + "a" * 40 + " 11111111111111111111111111111111")
        self.assertEqual(len(result), 3)
        self.assertIn({"kind": "cashtag", "value": "DOGE"}, result)
        self.assertFalse(make_alert(post(), NOW, 900)["token_identity_verified"])
        self.assertEqual(references("1" * 33), [])
        self.assertEqual(references("$X"), [{"kind": "cashtag", "value": "X"}])

    def test_old_and_future_posts_do_not_alert(self):
        self.assertIsNone(make_alert(post(age=901), NOW, 900))
        self.assertIsNone(make_alert(post(age=-61), NOW, 900))
        self.assertEqual(make_alert(post(text="a new meme"), NOW, 900)["interpretation"], "unclassified_post")

    def test_rejects_bad_timestamps_and_untrusted_urls(self):
        with self.assertRaises(ValueError):
            Post.from_dict({**post().to_dict(), "published_at": "2026-10-08T12:00:00"})
        self.assertEqual(
            Post.from_dict({**post().to_dict(), "url": "https://evil.example"}).url, "https://x.com/example/status/123"
        )

    def test_deduplicates_across_targets_and_restarts(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite"
        for target in ("first", "second"):
            store = Store(path)
            store.complete_poll(target, NOW, NOW, [post()], {"123": make_alert(post(), NOW, 900)}, "ok")
            store.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store.status()["posts"], 1)
        events = []
        self.assertEqual(store.deliver(events.append, NOW), 1)
        self.assertEqual(store.deliver(events.append, NOW), 0)
        self.assertEqual(events[0]["post"]["id"], "123")

    def test_failed_delivery_remains_pending_and_failed_poll_is_not_baseline(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        store = Store(Path(directory) / "monitor.sqlite")
        self.addCleanup(store.close)
        store.complete_poll("first", NOW, NOW, [post()], {"123": make_alert(post(), NOW, 900)}, "partial")
        self.assertFalse(store.has_baseline("first"))

        def broken_sink(event):
            raise OSError("disconnected")

        with self.assertRaises(OSError):
            store.deliver(broken_sink, NOW)
        self.assertEqual(store.status()["pending_alerts"], 1)

    def test_config_paths_and_invalid_options(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('state_dir = "state"\npoll_seconds = 120\n', encoding="utf-8")
            self.assertEqual(load_config(path).state_dir, Path(directory).resolve() / "state")
            for value in ("true", "nan", "0", "29", '"30"'):
                path.write_text(f"poll_seconds = {value}\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
