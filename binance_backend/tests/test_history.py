import asyncio
import time
from unittest.mock import patch

import httpx
import pytest

from app.config import Settings
from app.feed import BinanceSpotFeed
from app.history import STEP_MS, MarketHistory, observation


def candles(end: int) -> list[dict]:
    return [
        {
            "openTime": end - (240 - i) * 60_000,
            "close": 80000 + i % 3,
            "high": 80010,
            "low": 79990,
            "baseVolume": 2.0,
            "quoteVolume": 160000.0,
        }
        for i in range(240)
    ]


def test_observation_uses_closed_window_and_disjoint_volume():
    end = 30 * STEP_MS
    sample = candles(end)
    sample.append({**sample[-1], "openTime": end, "closed": False, "baseVolume": 9999})
    row = observation(sample, end)
    assert row is not None
    assert row["volumeBtc"] == 20
    assert row["volumeUsdt"] == 1_600_000
    assert 0 <= row["sidewaysScore"] <= 100
    assert row["volatilityAnnualizedPercent"] > 0
    sample[-2]["closed"] = False
    assert observation(sample, end) is None


@pytest.mark.parametrize("bad", ["gap", "duplicate", "nan", "missing_volume"])
def test_incomplete_or_invalid_history_is_not_recorded(bad):
    end = 30 * STEP_MS
    sample = candles(end)
    if bad == "gap":
        sample.pop(100)
    elif bad == "duplicate":
        sample[100] = sample[101]
    elif bad == "nan":
        sample[100]["close"] = float("nan")
    else:
        del sample[100]["baseVolume"]
    assert observation(sample, end) is None


def test_sqlite_restart_deduplication_and_symbol_isolation(tmp_path):
    path = str(tmp_path / "history.sqlite")
    end = 30 * STEP_MS
    history = MarketHistory(path, "BTCUSDT")
    history.initialize()
    row = observation(candles(end), end)
    history.save(row)
    history.save({**row, "volumeBtc": 999})
    assert MarketHistory(path, "BTCUSDT").read(end) == [row]
    assert MarketHistory(path, "ETHUSDT").read(end) == []
    assert history.read(end - 1) == []
    assert history.read(end + 51 * 3_600_000) == []


@pytest.mark.asyncio
async def test_background_recorder_writes_once_at_next_boundary(tmp_path):
    end = 30 * STEP_MS
    feed = BinanceSpotFeed(Settings(market_history_path=str(tmp_path / "history.sqlite")), None)
    feed.connected = True
    feed.last_event_at = end
    feed.analysis_candles = candles(end)
    original_save = feed.history.save

    def save(row):
        original_save(row)
        feed._stopping.set()

    with (
        patch("app.feed.time.time", side_effect=[(end - 1) / 1000, end / 1000]),
        patch.object(feed.history, "save", side_effect=save),
    ):
        await asyncio.wait_for(feed._record_history(), timeout=3)
    assert len(feed.history.read(end)) == 1


@pytest.mark.asyncio
async def test_history_endpoint_serves_persisted_rows_and_handles_uninitialized_store(tmp_path, monkeypatch):
    from app.main import app

    feed = BinanceSpotFeed(Settings(market_history_path=str(tmp_path / "history.sqlite")), None)
    monkeypatch.setattr(app.state, "feed", feed, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        unavailable = await client.get("/api/market/btcusd/history")
        assert unavailable.json()["available"] is False
        feed.history.initialize()
        end = int(time.time() * 1000) // STEP_MS * STEP_MS
        row = observation(candles(end), end)
        feed.history.save(row)
        response = await client.get("/api/market/btcusd/history")
        assert response.status_code == 200
        assert response.json()["observations"] == [row]
        assert response.json()["intervalMinutes"] == 10
