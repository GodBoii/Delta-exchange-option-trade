import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_evidence import NOW, post
from test_runner import StubCollector

from social_monitor.collector import Collection
from social_monitor.config import Config, Target, load_config
from social_monitor.relevance import classify
from social_monitor.runner import Runner
from social_monitor.store import Store


class RelevanceTests(unittest.TestCase):
    def test_confirmed_crypto_misses_and_unrelated_controls(self):
        for text in (
            "Qubic attack drains 1404 wallets; stolen funds frozen at MEXC",
            "Ledger received hardware wallet tampering reports",
            "5 wallets opened longs on $AGT on @Aster_DEX",
            "Perps and tokenized stocks fix this",
            "Circle's USDC is an e-money token under MiCA",
            "SOL and BONK trading on Binance",
            "A flash loan exploit drained liquidity pools",
            "Revoke suspicious token approvals; a wallet drainer is active",
            "SPX6900 community and $SPX",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify(post(text=text))["relevance"], "crypto")
        for text in (
            "My leather wallet was stolen",
            "The school network was hacked",
            "Tokenization of language models",
            "Optimism about an avalanche of good news",
            "@coinbase lovely dinner",
            "bonk the ball",
            "SOL is a nice name",
            "A phishing email stole my school login",
            "S&P 500 $SPX rallies on earnings",
        ):
            with self.subTest(text=text):
                self.assertNotEqual(classify(post(text=text))["relevance"], "crypto")
        assessment = classify(post(text="5 wallets opened longs on $AGT on @Aster_DEX"))
        self.assertEqual(assessment["unresolved_cashtags"], ["AGT"])
        self.assertEqual(assessment["assets"], [])
        self.assertEqual(classify(post(text="SOL and BONK trading on Binance"))["assets"], ["BONK", "SOL"])
        meme = classify(post(text="SPX6900 community and $SPX"))
        self.assertEqual(meme["unresolved_cashtags"], ["SPX"])
        self.assertEqual(meme["assets"], [])

    def test_relevance_does_not_infer_direction_or_contract_identity(self):
        for text in (
            "Bitcoin ETF approval",
            "Ethereum exploit",
            "$DOGE is not endorsed",
            "zkSync milestone",
            "A new memecoin",
            "0x" + "a" * 40,
        ):
            result = classify(post(text=text))
            self.assertEqual(result["relevance"], "crypto", text)
            self.assertEqual(result["direction"], "unclear")
            self.assertFalse(result["token_identity_verified"])

    def test_stock_tags_urls_and_personal_posts_are_not_crypto_alerts(self):
        for text in ("$AAPL beats earnings", "Lovely dinner", "@bitcoin great photo", "https://example.com/bitcoin"):
            self.assertNotEqual(classify(post(text=text))["relevance"], "crypto", text)
        self.assertEqual(classify(post(text="$NEWCOIN"))["unresolved_cashtags"], ["NEWCOIN"])

    def test_quote_repost_and_missing_reply_context(self):
        result = classify(replace(post(text="Interesting"), quoted_id="777", quoted_text="Ethereum upgrade"))
        self.assertEqual(result["assets"], ["ETH"])
        self.assertEqual(result["evidence"][0]["source_post_id"], "777")
        result = classify(replace(post(text=""), reposted_id="888", reposted_text="$PEPE"))
        self.assertEqual(result["assets"], ["PEPE"])
        self.assertEqual(classify(replace(post(text="Yes"), reply_to="999"))["relevance"], "uncertain")
        self.assertEqual(
            classify(replace(post(text=""), media_urls=("https://example.com/photo",)))["relevance"], "uncertain"
        )

    def test_crypto_roster_and_backward_compatible_config(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config.crypto.toml")
        self.assertTrue(config.crypto_only)
        self.assertEqual(len(config.targets), 24)
        self.assertNotIn("elonmusk", {t.value for t in config.targets})
        new_targets = [target for target in config.targets if target.expected_author_id]
        self.assertEqual(len(new_targets), 12)
        self.assertTrue(all(target.include_replies for target in config.targets))
        self.assertEqual(len({target.expected_author_id for target in new_targets}), 12)
        requests_per_window = sum(900 / config.interval(target) for target in config.targets)
        reconciliation_overhead = len(config.targets) * (config.max_pages - 1) * 900 / config.reconcile_seconds
        self.assertLessEqual(requests_per_window + reconciliation_overhead, 200)
        self.assertEqual(
            {target.name for target in config.targets if config.interval(target) == 60},
            {"cz", "vitalik", "lookonchain", "wu"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("", encoding="utf-8")
            self.assertFalse(load_config(path).crypto_only)
            path.write_text('crypto_only = "yes"', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)
            for value in ('"not-numeric"', "123", "true", '""'):
                path.write_text(
                    f'[[targets]]\nname="one"\nkind="user"\nvalue="example"\nexpected_author_id={value}\n',
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    load_config(path)


class RelevancePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_posts_are_not_rescreened_or_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(root, (Target("one", "user", "example"),), alert_on_first_poll=True)
            store = Store(root / "monitor.sqlite")
            try:
                old = {"version": 1, "relevance": "uncertain", "study_eligible": False}
                item = post(text="Qubic exploit")
                store.complete_poll(config.targets[0].key, NOW, NOW, [item], {}, "ok", assessments={item.id: old})
                with patch("social_monitor.runner.classify", side_effect=AssertionError("known post was rescreened")):
                    self.assertTrue(
                        await Runner(
                            config,
                            store,
                            StubCollector(Collection([item])),
                            lambda e: self.fail("duplicate output"),
                            lambda e: None,
                            lambda: NOW,
                        ).run(once=True)
                    )
                self.assertEqual(
                    json.loads(store.connection.execute("SELECT payload FROM assessments").fetchone()[0]), old
                )
            finally:
                store.close()

    async def test_filter_preserves_raw_evidence_controls_and_restart_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            target = Target("example", "user", "example")
            config = Config(state, (target,), crypto_only=True, alert_on_first_poll=True)
            items = [post("101", "Bitcoin ETF"), post("102", "Dinner time"), post("103", "$NEWCOIN")]
            events = []
            for _ in range(2):
                store = Store(state / "monitor.sqlite")
                try:
                    await Runner(
                        config, store, StubCollector(Collection(items)), events.append, lambda event: None, lambda: NOW
                    ).run(once=True)
                    self.assertEqual(store.status()["posts"], 3)
                    assessments = [
                        json.loads(row[0]) for row in store.connection.execute("SELECT payload FROM assessments")
                    ]
                    self.assertEqual({a["relevance"] for a in assessments}, {"crypto", "uncertain", "unrelated"})
                    self.assertTrue(all(a["study_eligible"] for a in assessments))
                finally:
                    store.close()
            self.assertEqual([e["post"]["id"] for e in events], ["101"])

    async def test_baseline_and_stale_posts_are_not_new_study_events(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            target = Target("example", "user", "example")
            store = Store(state / "monitor.sqlite")
            try:
                config = Config(state, (target,), crypto_only=True)
                for item in (post("101"), post("102", age=901)):
                    await Runner(
                        config,
                        store,
                        StubCollector(Collection([item])),
                        lambda e: self.fail("unexpected alert"),
                        lambda e: None,
                        lambda: NOW,
                    ).run(once=True)
                self.assertTrue(
                    all(
                        not json.loads(row[0])["study_eligible"]
                        for row in store.connection.execute("SELECT payload FROM assessments")
                    )
                )
            finally:
                store.close()
