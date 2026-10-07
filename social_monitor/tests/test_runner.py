import asyncio
import io
import json
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from getpass import GetPassWarning
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_evidence import NOW, post

from social_monitor.cli import main
from social_monitor.collector import Collection, XCollector, normalize
from social_monitor.config import Config, Target
from social_monitor.locking import writer_lock
from social_monitor.runner import Runner
from social_monitor.store import Store


class StubCollector:
    def __init__(self, result):
        self.result = result

    async def fetch(self, target, limit):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.state = Path(directory)
        self.store = Store(self.state / "monitor.sqlite")
        self.addCleanup(self.store.close)
        self.target = Target("example", "user", "example")
        self.config = Config(self.state, (self.target,))
        self.events, self.diagnostics = [], []

    def runner(self, collector):
        return Runner(self.config, self.store, collector, self.events.append, self.diagnostics.append, lambda: NOW)

    async def test_baseline_then_new_posts_only_with_restart(self):
        self.assertTrue(await self.runner(StubCollector(Collection([post()]))).run(once=True))
        self.assertEqual(self.events, [])
        self.assertTrue(await self.runner(StubCollector(Collection([post(), post("124")]))).run(once=True))
        self.assertEqual([event["post"]["id"] for event in self.events], ["124"])
        self.assertTrue(await self.runner(StubCollector(Collection([post("124")]))).run(once=True))
        self.assertEqual(len(self.events), 1)

    async def test_failure_does_not_establish_baseline_or_leak_secrets(self):
        result = await self.runner(StubCollector(RuntimeError("auth_token=secret"))).run(once=True)
        self.assertFalse(result)
        self.assertFalse(self.store.has_baseline(self.target.key))
        self.assertNotIn("secret", json.dumps(self.diagnostics) + json.dumps(self.store.status()))
        self.assertEqual(self.store.status()["targets"][0]["error_code"], "RuntimeError")

    async def test_timeout_and_cancellation(self):
        class Hanging:
            async def fetch(self, target, limit):
                await asyncio.sleep(60)

        self.config = replace(self.config, timeout_seconds=0.01)
        self.assertFalse(await self.runner(Hanging()).run(once=True))
        self.assertEqual(self.store.status()["targets"][0]["error_code"], "TimeoutError")

        class Cancelled:
            async def fetch(self, target, limit):
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.runner(Cancelled()).run(once=True)

    async def test_installed_collector_fails_without_session_before_network(self):
        with closing(XCollector(self.state / "accounts.sqlite")) as collector:
            self.assertFalse(await self.runner(collector).run(once=True))
        self.assertEqual(self.store.status()["targets"][0]["error_code"], "NoAccountError")

    async def test_collector_filters_other_authors_and_closes_stream_at_limit(self):
        closed = []
        media = SimpleNamespace(photos=[], videos=[], animated=[])
        tweet = SimpleNamespace(
            id=123,
            user=SimpleNamespace(id=456, username="example"),
            rawContent="$DOGE",
            date=NOW,
            inReplyToTweetId=None,
            quotedTweet=None,
            retweetedTweet=None,
            media=media,
        )

        async def stream(uid, limit):
            try:
                yield SimpleNamespace(user=SimpleNamespace(id=789))
                yield tweet
                self.fail("collector read past the requested limit")
            finally:
                closed.append(True)

        with closing(XCollector(self.state / "accounts.sqlite")) as collector:
            collector.api = SimpleNamespace(
                user_by_login=AsyncMock(return_value=SimpleNamespace(id=456)),
                user_tweets_and_replies=stream,
            )
            result = await collector.fetch(self.target, 1)
        self.assertEqual([item.id for item in result.posts], ["123"])
        self.assertEqual(closed, [True])

    async def test_empty_upstream_is_unverified(self):
        async def empty(query, limit, kv):
            for tweet in []:
                yield tweet

        with closing(XCollector(self.state / "accounts.sqlite")) as collector:
            collector.api = SimpleNamespace(search=empty)
            result = await collector.fetch(Target("search", "search", "$DOGE"), 40)
        self.assertEqual((result.status, result.error_code), ("failed", "empty_unverified"))


class InterfaceTests(unittest.TestCase):
    def test_auth_refuses_visible_input_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text('state_dir="state"\n', encoding="utf-8")
            with (
                patch("social_monitor.cli.sys.stdin.isatty", return_value=True),
                patch("social_monitor.cli.getpass", side_effect=GetPassWarning("cannot hide input")),
                redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(main(["--config", str(config), "auth"]), 2)
            self.assertIn("hidden input is unavailable", errors.getvalue())
            self.assertFalse((root / "state").exists())

    def test_normalization_retains_reply_quote_repost_and_media(self):
        tweet = SimpleNamespace(
            id=123,
            user=SimpleNamespace(id=456, username="example"),
            rawContent="$DOGE",
            date=NOW,
            inReplyToTweetId=12,
            quotedTweet=SimpleNamespace(id=13, rawContent="$SHIB"),
            retweetedTweet=SimpleNamespace(id=14, rawContent="$PEPE"),
            media=SimpleNamespace(
                photos=[SimpleNamespace(url="https://pbs.twimg.com/example.jpg")], videos=[], animated=[]
            ),
        )
        item = normalize(tweet)
        self.assertEqual((item.reply_to, item.quoted_id, item.reposted_id), ("12", "13", "14"))
        self.assertEqual(item.media_urls, ("https://pbs.twimg.com/example.jpg",))
        self.assertEqual(item.quoted_text, "$SHIB")

    def test_writer_lock_is_exclusive_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with writer_lock(state), self.assertRaises(ValueError), writer_lock(state):
                self.fail("second writer acquired lock")
            with writer_lock(state):
                self.assertTrue((state / "writer.lock").exists())

    def test_replay_cli_is_deduplicated_and_export_excludes_session_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text('state_dir="state"\n', encoding="utf-8")
            fixture = root / "input.jsonl"
            fixture.write_text(json.dumps(post().to_dict()), encoding="utf-8")
            command = ["--config", str(config), "replay", str(fixture), "--at", NOW.isoformat()]
            with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                self.assertEqual(main(command), 0)
                self.assertEqual(main(command), 0)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--config", str(config), "export"]), 0)
            self.assertEqual(json.loads(output.getvalue())["id"], "123")
            self.assertFalse((root / "state" / "accounts.sqlite").exists())

    def test_invalid_replay_does_not_write_partial_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text('state_dir="state"\n', encoding="utf-8")
            fixture = root / "input.jsonl"
            fixture.write_text(json.dumps(post().to_dict()) + '\n{"bad":true}', encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                result = main(["--config", str(config), "replay", str(fixture), "--at", NOW.isoformat()])
            self.assertEqual(result, 2)
            self.assertFalse((root / "state").exists())
