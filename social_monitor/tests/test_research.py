import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from test_evidence import NOW, post

from social_monitor.market import MINUTE, BinanceCandles, Candle, MarketError, baseline_open, measure, parse_candles
from social_monitor.relevance import classify
from social_monitor.research import Research, status
from social_monitor.store import Store


class MarketTests(unittest.TestCase):
    def test_candle_anchors_exclude_the_containing_minute(self):
        published = NOW + timedelta(seconds=40)
        received = NOW + timedelta(minutes=1, seconds=20)
        start = baseline_open(published) - 5 * MINUTE
        candles = [Candle(start + n * MINUTE, start + (n + 1) * MINUTE - 1, 100 + n, 10) for n in range(12)]
        result = measure(candles, published, received, 5)
        self.assertLess(result["anchors"]["publication"]["closed_ms"], published.timestamp() * 1000)
        self.assertEqual(result["anchors"]["publication"]["close"], 105)
        self.assertEqual(result["anchors"]["receipt"]["close"], 106)
        self.assertAlmostEqual(result["receipt_return_pct"], (111 / 106 - 1) * 100)

    def test_missing_unordered_invalid_or_zero_volume_data_does_not_become_zero_return(self):
        start = baseline_open(NOW)
        row = [start, "1", "1", "1", "1", "2", start + MINUTE - 1]
        self.assertEqual(len(parse_candles([row], start, start)), 1)
        for rows in ([], [row, row], [{"bad": 1}], [[*row[:4], "NaN", *row[5:]]], [[*row[:4], "0", *row[5:]]]):
            with self.assertRaises(MarketError):
                parse_candles(rows, start, start)
        candles = [Candle(start + n * MINUTE, start + (n + 1) * MINUTE - 1, 100, 0) for n in range(-5, 2)]
        with self.assertRaisesRegex(MarketError, "stale_no_trade_anchor"):
            measure(candles, NOW, NOW, 1)

    def test_client_uses_only_public_market_url_and_validates_response(self):
        start = baseline_open(NOW)
        row = [start, "1", "1", "1", "2", "3", start + MINUTE - 1]
        with patch("social_monitor.market.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps([row]).encode()
            self.assertEqual(BinanceCandles().fetch("BTC", start, start)[0].close, 2)
            url = request.call_args.args[0]
            self.assertTrue(url.startswith("https://data-api.binance.vision/api/v3/klines?"))
            self.assertIn("symbol=BTCUSDT", url)
            self.assertNotIn("signature", url)


class FakeMarket:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def fetch(self, asset, start, end):
        self.calls.append(asset)
        if asset == self.fail:
            raise MarketError("market_rate_limited", 300)
        return [Candle(t, t + MINUTE - 1, 100 + n, 1) for n, t in enumerate(range(start, end + 1, MINUTE))]


class ResearchTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.source = Store(self.root / "monitor.sqlite")
        self.addCleanup(self.source.close)
        self.research = Research(self.root / "research" / "market.sqlite")
        self.addCleanup(lambda: self.research.close())

    def ingest(self, item, eligible=True):
        assessment = {**classify(item), "study_eligible": eligible}
        self.source.complete_poll("example", NOW, NOW, [item], {}, "ok", assessments={item.id: assessment})

    def test_restart_deduplication_controls_horizons_and_benchmarks(self):
        self.ingest(post("101", "$DOGE"))
        self.ingest(post("102", "dinner"))
        self.ingest(post("103"), eligible=False)
        self.assertEqual(self.research.discover(self.source.connection), 2)
        self.assertEqual(self.research.discover(self.source.connection), 0)
        events = []
        self.assertEqual(self.research.cycle(FakeMarket(), events.append, NOW), 0)
        self.assertEqual(self.research.cycle(FakeMarket(), events.append, NOW + timedelta(minutes=2)), 2)
        control = next(e for e in events if e["post_id"] == "102")
        self.assertEqual(control["assessment"]["relevance"], "unrelated")
        self.assertEqual(set(control["markets"]), {"BTC", "ETH"})
        doge = next(e for e in events if e["post_id"] == "101")
        self.assertIn("BTC", doge["markets"]["DOGE"]["receipt_excess_vs_benchmark_pct_points"])
        self.assertFalse(doge["executable_return"])
        self.research.close()
        self.research = Research(self.root / "research" / "market.sqlite")
        self.assertEqual(self.research.discover(self.source.connection), 0)
        self.assertEqual(self.research.cycle(FakeMarket(), events.append, NOW + timedelta(minutes=61)), 6)
        self.assertEqual(len(events), 8)
        self.assertEqual(self.research.cycle(FakeMarket(), events.append, NOW + timedelta(minutes=62)), 0)

    def test_rate_limit_persists_global_cooldown_and_partial_measurements(self):
        self.ingest(post("101", "$DOGE"))
        self.research.discover(self.source.connection)
        events = []
        client = FakeMarket(fail="DOGE")
        at = NOW + timedelta(minutes=61)
        self.research.cycle(client, events.append, at)
        self.assertEqual(client.calls, ["BTC", "DOGE"])
        self.assertEqual(events[0]["status"], "pending")
        self.assertIn("BTC", events[0]["markets"])
        self.assertEqual(self.research.cycle(FakeMarket(), events.append, at + timedelta(seconds=299)), 0)
        client = FakeMarket()
        self.research.cycle(client, events.append, at + timedelta(seconds=301))
        self.assertEqual(client.calls.count("BTC"), 3)  # Already saved BTC for one of four horizons.

    def test_unknown_tokens_are_not_assigned_an_invented_market(self):
        self.ingest(post("101", "new memecoin $UNKNOWN"))
        self.research.discover(self.source.connection)
        events = []
        client = FakeMarket()
        self.research.cycle(client, events.append, NOW + timedelta(minutes=2))
        self.assertEqual(client.calls, ["BTC", "ETH"])
        self.assertEqual(events[0]["unresolved_references"], ["UNKNOWN"])

    def test_existing_evidence_and_pending_alerts_survive_additive_schema(self):
        self.source.complete_poll("legacy", NOW, NOW, [post()], {"123": {"legacy": True}}, "ok")
        self.source.connection.execute("DROP TABLE assessments")
        self.source.close()
        self.source = Store(self.root / "monitor.sqlite")
        self.addCleanup(self.source.close)
        self.assertEqual(self.source.status()["posts"], 1)
        self.assertEqual(self.source.status()["pending_alerts"], 1)
        self.assertEqual(self.research.discover(self.source.connection), 0)
        self.assertFalse(status(self.root / "missing.sqlite")["healthy"])
