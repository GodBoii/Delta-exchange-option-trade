import time
from typing import cast

import pytest

from app.config import Settings
from app.feed import BinanceSpotFeed, OrderBookSequenceGap, replace_latest_candle


def feed() -> BinanceSpotFeed:
    return BinanceSpotFeed(Settings(), cast(object, None))  # type: ignore[arg-type]


def test_synchronizes_snapshot_and_buffered_depth_events() -> None:
    market_feed = feed()
    snapshot = {
        "lastUpdateId": 100,
        "bids": [[60_000.0, 1.0], [59_990.0, 2.0]],
        "asks": [[60_010.0, 1.5]],
    }
    events = [
        {"U": 99, "u": 100, "b": [["60000", "9"]], "a": []},
        {"U": 101, "u": 102, "b": [["60000", "0"], ["59995", "3"]], "a": [["60010", "2"]]},
        {"U": 103, "u": 103, "b": [], "a": [["60020", "4"]]},
    ]

    assert market_feed._synchronize_book(snapshot, events) is True
    assert market_feed.book_synced is True
    assert market_feed.book_update_id == 103
    assert market_feed.book_levels(5) == (
        [[59_995.0, 3.0], [59_990.0, 2.0]],
        [[60_010.0, 2.0], [60_020.0, 4.0]],
    )


def test_waits_for_bridge_event_and_rejects_sequence_gap() -> None:
    market_feed = feed()
    snapshot = {"lastUpdateId": 100, "bids": [], "asks": []}

    assert market_feed._synchronize_book(snapshot, [{"U": 90, "u": 100}]) is False
    with pytest.raises(OrderBookSequenceGap):
        market_feed._synchronize_book(snapshot, [{"U": 102, "u": 103}])


def test_depth_updates_delete_zero_quantity_and_detect_gap() -> None:
    market_feed = feed()
    market_feed.book_update_id = 10
    market_feed.bids = {60_000.0: 1.0}
    market_feed.asks = {}

    market_feed._apply_depth({"U": 11, "u": 11, "b": [["60000", "0"]], "a": [["60010", "2"]]})
    assert market_feed.bids == {}
    assert market_feed.asks == {60_010.0: 2.0}

    with pytest.raises(OrderBookSequenceGap):
        market_feed._apply_depth({"U": 13, "u": 13, "b": [], "a": []})


def test_replaces_open_candle_without_growing_history() -> None:
    candles = [{"openTime": 1, "close": 10.0}]
    replace_latest_candle(candles, {"openTime": 1, "close": 11.0}, 2)
    replace_latest_candle(candles, {"openTime": 2, "close": 12.0}, 2)
    replace_latest_candle(candles, {"openTime": 3, "close": 13.0}, 2)

    assert candles == [{"openTime": 2, "close": 12.0}, {"openTime": 3, "close": 13.0}]


def test_exposes_synchronized_depth_and_recent_aggressor_trades() -> None:
    market_feed = feed()
    market_feed.connected = True
    market_feed.book_synced = True
    market_feed.book_update_id = 44
    market_feed.bids = {64_800.0: 1.5}
    market_feed.asks = {64_801.0: 2.5}
    now = int(time.time() * 1000)
    market_feed._handle_trade({"t": 7, "p": "64800.5", "q": "0.25", "T": now - 1, "m": False})
    market_feed._handle_trade({"t": 8, "p": "64800.0", "q": "0.10", "T": now, "m": True})

    snapshot = market_feed.snapshot()

    assert snapshot["orderBook"] == {
        "lastUpdateId": 44,
        "eventTime": 0,
        "bids": [[64_800.0, 1.5]],
        "asks": [[64_801.0, 2.5]],
    }
    assert snapshot["recentTrades"][0]["side"] == "sell"
    assert snapshot["recentTrades"][1]["side"] == "buy"
    assert snapshot["analysis"]["cvd"]["baseVolume"] == pytest.approx(0.15)


def test_cvd_expires_against_wall_clock_without_another_trade(monkeypatch) -> None:
    market_feed = feed()
    monkeypatch.setattr("app.feed.time.time", lambda: 2000)
    market_feed._handle_trade({"t": 1, "p": "100", "q": "2.5", "T": 1_099_000, "m": False})
    assert market_feed.snapshot()["analysis"]["cvd"]["baseVolume"] == 0
    assert not market_feed.trade_deltas


def test_cvd_combines_same_millisecond_and_keeps_incremental_total(monkeypatch) -> None:
    market_feed = feed()
    monkeypatch.setattr("app.feed.time.time", lambda: 2000)
    market_feed._handle_trade({"t": 1, "p": "100", "q": "2", "T": 2_000_000, "m": False})
    market_feed._handle_trade({"t": 2, "p": "100", "q": "0.5", "T": 2_000_000, "m": True})
    assert len(market_feed.trade_deltas) == 1
    assert market_feed.snapshot()["analysis"]["cvd"]["baseVolume"] == pytest.approx(1.5)


def test_top_levels_match_full_sort_without_discarding_deeper_levels() -> None:
    market_feed = feed()
    market_feed.bids = {float(x): float(x + 1) for x in range(5000)}
    market_feed.asks = {float(x): float(x + 2) for x in range(5000, 10000)}
    expected = ([[p, q] for p, q in sorted(market_feed.bids.items(), reverse=True)[:50]],
                [[p, q] for p, q in sorted(market_feed.asks.items())[:50]])
    assert market_feed.book_levels(50) == expected
    assert len(market_feed.bids) == len(market_feed.asks) == 5000
