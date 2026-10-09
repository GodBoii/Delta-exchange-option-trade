import json
import tempfile
import time
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_evidence import NOW, post

from social_monitor.collector import Collection, XCollector
from social_monitor.config import Config, Target
from social_monitor.runner import Runner
from social_monitor.store import Store


def tweet(number):
    return SimpleNamespace(
        id=number,
        user=SimpleNamespace(id=456, username="example"),
        rawContent="$DOGE",
        date=NOW,
        inReplyToTweetId=None,
        quotedTweet=None,
        retweetedTweet=None,
        media=SimpleNamespace(photos=[], videos=[], animated=[]),
    )


def response(numbers=(), *, status=200, headers=None, errors=None, valid=True):
    return SimpleNamespace(
        status_code=status,
        headers=headers or {},
        json=lambda: {
            "data": {} if valid else None,
            "items": [tweet(number) for number in numbers],
            "errors": errors or [],
        },
    )


class PageSource:
    def __init__(self, pages, before_page=None):
        self.pages = pages
        self.reads = 0
        self.closed = False
        self.before_page = before_page
        self.user_by_login = AsyncMock(return_value=SimpleNamespace(id=456))

    async def user_tweets_and_replies_raw(self, uid, limit, kv):
        try:
            for page in self.pages:
                self.reads += 1
                if self.before_page:
                    self.before_page()
                yield page
        finally:
            self.closed = True


class IncrementalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.target = Target("example", "user", "example")

    def collector(self, source, *, max_pages=4):
        collector = XCollector(self.root / "accounts.sqlite", max_pages=max_pages)
        self.addCleanup(collector.close)
        collector.api = source
        collector.parse_page = lambda body: body["items"]
        return collector

    async def test_fast_poll_stops_after_overlap_and_releases_iterator(self):
        source = PageSource([response([1, 2, 3, 4]), response([5, 6])])
        result = await self.collector(source).fetch(
            self.target, 80, known_ids=frozenset({"1", "2", "3"}), reconcile=False
        )
        self.assertEqual(source.reads, 1)
        self.assertTrue(source.closed)
        self.assertEqual({item.id for item in result.posts}, {"1", "2", "3", "4"})
        self.assertEqual(result.details["stop_reason"], "known_overlap")

    async def test_deep_check_continues_past_known_posts_but_has_page_bound(self):
        source = PageSource([response([1, 2, 3]), response([4, 5, 6]), response([7])])
        result = await self.collector(source, max_pages=2).fetch(
            self.target,
            80,
            known_ids=frozenset({"1", "2", "3"}),
            reconcile=True,
        )
        self.assertEqual(source.reads, 2)
        self.assertEqual({item.id for item in result.posts}, {"1", "2", "3", "4", "5", "6"})
        self.assertEqual(result.details["stop_reason"], "page_limit")

    async def test_one_known_pinned_post_does_not_stop_a_burst_read(self):
        source = PageSource([response([1, 10, 11]), response([2, 3, 4])])
        result = await self.collector(source).fetch(
            self.target, 80, known_ids=frozenset({"1", "2", "3", "4"}), reconcile=False
        )
        self.assertEqual(source.reads, 2)
        self.assertIn("11", {item.id for item in result.posts})

    async def test_cosmetic_card_warning_is_not_a_failed_post_collection(self):
        source = PageSource([response([1])])
        collector = self.collector(source)
        source.before_page = lambda: collector._warning(
            SimpleNamespace(
                record={
                    "name": "twscrape.models",
                    "message": "Unknown card type secret-auth_token",
                }
            )
        )
        result = await collector.fetch(self.target, 80)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.details["warnings"], ["unsupported_card"])
        self.assertNotIn("secret", json.dumps(result.details))

    async def test_authentication_error_is_recognized_even_without_data(self):
        source = PageSource([response(valid=False, errors=[{"code": 32, "message": "auth_token=secret"}])])
        result = await self.collector(source).fetch(self.target, 80)
        self.assertEqual(result.error_code, "authentication_required")
        self.assertNotIn("secret", json.dumps(result.details))

    async def test_quota_reserve_stops_future_network_calls_until_reset(self):
        reset = int(time.time()) + 120
        source = PageSource(
            [
                response(
                    [1, 2, 3],
                    headers={
                        "x-rate-limit-limit": "500",
                        "x-rate-limit-remaining": "5",
                        "x-rate-limit-reset": str(reset),
                    },
                ),
                response([4]),
            ]
        )
        collector = self.collector(source)
        first = await collector.fetch(self.target, 80)
        second = await collector.fetch(self.target, 80)
        self.assertEqual(first.status, "ok")
        self.assertGreater(first.retry_after_seconds, 110)
        self.assertEqual(second.error_code, "rate_limited")
        self.assertEqual(source.reads, 1)

    async def test_429_honors_retry_after(self):
        source = PageSource([response(status=429, headers={"retry-after": "90"})])
        result = await self.collector(source).fetch(self.target, 80)
        self.assertEqual(result.error_code, "rate_limited")
        self.assertGreater(result.retry_after_seconds, 85)

    async def test_blocked_session_is_stopped_before_another_target_request(self):
        source = PageSource([response(status=403)])
        collector = self.collector(source)
        with closing(Store(self.root / "monitor.sqlite")) as store:
            config = Config(self.root, (self.target, Target("second", "user", "other")))
            runner = Runner(config, store, collector, lambda event: None, lambda event: None)
            self.assertFalse(await runner.run(once=True))
            self.assertEqual(runner.stop_reason, "access_blocked")
            self.assertEqual(source.reads, 1)

    def test_partial_deep_read_does_not_repeat_deep_reads_every_fast_cycle(self):
        with closing(Store(self.root / "monitor.sqlite")) as store:
            store.complete_poll(self.target.key, NOW, NOW, [post()], {}, "partial", "parser_error", reconciled=True)
            self.assertFalse(store.reconciliation_due(self.target.key, NOW + timedelta(seconds=15), 900))
            self.assertTrue(store.reconciliation_due(self.target.key, NOW + timedelta(seconds=901), 900))
            self.assertFalse(store.has_baseline(self.target.key))


class VirtualClock:
    def __init__(self):
        self.seconds = 0.0

    async def sleep(self, seconds):
        self.seconds += seconds

    def monotonic(self):
        return self.seconds

    def now(self):
        return NOW + timedelta(seconds=self.seconds)


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_intervals_do_not_drift_with_request_duration(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        clock = VirtualClock()
        calls = []

        class Source:
            async def fetch(self, target, limit, **options):
                calls.append((target.name, clock.seconds))
                clock.seconds += 3
                return Collection([], "failed", "authentication_required") if len(calls) == 6 else Collection([post()])

        with closing(Store(Path(directory) / "monitor.sqlite")) as store:
            config = Config(
                Path(directory), (Target("one", "user", "one"), Target("two", "user", "two", poll_seconds=30))
            )
            runner = Runner(
                config,
                store,
                Source(),
                lambda event: None,
                lambda event: None,
                clock.now,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            self.assertFalse(await runner.run())
        self.assertEqual(calls, [("one", 0), ("two", 3), ("one", 15), ("one", 30), ("two", 33), ("one", 45)])

    async def test_cooldown_on_one_target_does_not_starve_another(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        clock = VirtualClock()
        calls = []

        class Source:
            async def fetch(self, target, limit, **options):
                calls.append((target.name, clock.seconds))
                if len(calls) == 1:
                    return Collection([], "failed", "upstream_busy", retry_after_seconds=120)
                if len(calls) == 5:
                    return Collection([], "failed", "authentication_required")
                return Collection([post()])

        with closing(Store(Path(directory) / "monitor.sqlite")) as store:
            config = Config(Path(directory), (Target("one", "user", "one"), Target("two", "user", "two")))
            runner = Runner(
                config,
                store,
                Source(),
                lambda event: None,
                lambda event: None,
                clock.now,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            self.assertFalse(await runner.run())
        self.assertEqual(calls, [("one", 0), ("two", 2), ("two", 17), ("two", 32), ("two", 47)])
