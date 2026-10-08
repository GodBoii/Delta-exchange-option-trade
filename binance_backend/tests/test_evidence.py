import time

import httpx
import pytest

from app.config import Settings
from app.enrichment import PublicEvidence
from app.evidence import LiquidityWindow, depth_summary, normalize_option, option_overview
from app.evidence_store import EvidenceStore
from app.history import RETENTION_MS


def option(now, asset="BTC", days=1):
    from datetime import UTC, datetime, time, timedelta
    from zoneinfo import ZoneInfo
    symbol = f"C-{asset}-100-010130"
    local = datetime.fromtimestamp(now / 1000, UTC).astimezone(ZoneInfo("Asia/Kolkata"))
    boundary = datetime.combine(local.date(), time(17, 30), local.tzinfo)
    if local >= boundary:
        boundary += timedelta(days=1)
    expiry = boundary + timedelta(days=days - 1)
    product = {"id": 1, "symbol": symbol, "settlement_time": expiry.astimezone(UTC).isoformat(),
               "contract_value": "0.001"}
    raw = {"product_id": 1, "symbol": symbol, "strike_price": 100, "spot_price": 100, "mark_vol": "0.4",
           "timestamp": now * 1000, "quotes": {"best_bid": 2, "best_ask": 3, "bid_size": 10, "ask_size": 10}}
    return normalize_option(raw, product, asset, now)


def test_option_units_missing_values_and_asset_validation():
    now = int(time.time() * 1000)
    row = option(now)
    assert row["impliedVolatility"] == 0.4
    assert row["observedAt"] == now
    assert row["vega"] is None and row["openInterest"] is None
    summary = option_overview([row], now)[0]
    assert summary["atmIvPercent"] == 40
    assert summary["oiBase"] is None
    assert option_overview([row], now + 100_000) == []
    with pytest.raises(ValueError):
        normalize_option({"symbol": "C-ETH-100-010130"}, {}, "BTC", now)


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
@pytest.mark.parametrize("at", ["2026-10-08T00:00:00Z", "2026-10-08T10:00:00Z", "2026-10-08T13:30:00Z"])
def test_overview_includes_only_upcoming_expiry_without_later_fallback(asset, at):
    from datetime import datetime

    now = int(datetime.fromisoformat(at).timestamp() * 1000)
    current = option(now, asset)
    later = option(now, asset, days=2)
    overview = option_overview([current, later, option(now, asset, days=7)], now)
    assert [r["expiry"] for r in overview] == [current["expiry"]]
    assert option_overview([later], now) == []


def test_assigned_legacy_expiry_is_available_only_when_explicitly_requested():
    now = int(time.time() * 1000)
    later = option(now, days=2)
    assert option_overview([later], now) == []
    assert option_overview([later], now, {later["expiryMs"]})[0]["expiry"] == later["expiry"]


def test_depth_distances_coverage_and_window_extremes():
    sample = depth_summary({99: 5, 99.95: 2}, {100.05: 3, 101: 7})
    assert sample["bands"]["0.1"]["bidBase"] == 2
    assert sample["bands"]["0.1"]["complete"] is True
    partial = depth_summary({99.95: 2}, {100.05: 3})
    assert partial["bands"]["0.1"]["complete"] is False
    touched_outer = depth_summary({90: 1, 99.95: 2}, {100.05: 3, 110: 1}, 99.95, 100.05)
    assert touched_outer["bands"]["1.0"]["complete"] is False
    assert depth_summary({101: 1}, {100: 1}) == {}
    window = LiquidityWindow(0)
    window.add(sample)
    window.add({**sample, "spreadBps": 20})
    result = window.summary(10_000)
    assert result["samples"] == 2 and result["coveragePercent"] == 20
    assert result["spreadMaximumBps"] == 20
    assert result["bands"]["0.1"]["bidMinimumBase"] == 2


def test_store_restart_isolation_retention_and_watchlist(tmp_path):
    path = str(tmp_path / "evidence.sqlite")
    btc, eth = EvidenceStore(path, "BTC"), EvidenceStore(path, "ETH")
    btc.initialize()
    now = RETENTION_MS + 1000
    btc.save("option_observations", 999, [("contract", {"x": 1})])
    eth.save("option_observations", 999, [("contract", {"x": 2})])
    btc.save("option_observations", 1000, [("contract", {"x": 3})])
    btc.save("option_observations", 1000, [("contract", {"x": 999})])
    btc.watch([now + 1000], now)
    btc.prune(now)
    assert EvidenceStore(path, "BTC").read("option_observations", 0, now) == [{"x": 3}]
    assert eth.read("option_observations", 0, now) == [{"x": 2}]
    assert EvidenceStore(path, "BTC").watched(now) == {now + 1000}
    with pytest.raises(ValueError):
        btc.save("observations; DROP TABLE observations", now, [])


@pytest.mark.asyncio
async def test_source_cooldown_and_optional_futures_failure(tmp_path):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={"Retry-After": "120"})
    client = PublicEvidence(Settings(market_history_path=str(tmp_path / "e.sqlite")), httpx.MockTransport(handler))
    client.store.initialize()
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.refresh_futures()
        with pytest.raises(ValueError, match="cooldown"):
            await client.refresh_futures()
        assert len(calls) == 1
        assert client.futures_summary({}, int(time.time() * 1000)) == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_optional_depth_failure_does_not_hide_fresh_quotes(tmp_path):
    from datetime import UTC, datetime, timedelta
    now = int(time.time() * 1000)
    symbol = "C-BTC-100-010130"
    def handler(request):
        if "l2orderbook" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json={"success": True, "result": {
            "symbol": symbol, "product_id": 1, "strike_price": 100, "spot_price": 100,
            "timestamp": now * 1000, "quotes": {"best_bid": 9, "best_ask": 10}}})
    client = PublicEvidence(Settings(market_history_path=str(tmp_path / "e.sqlite")), httpx.MockTransport(handler))
    client.products = {symbol: {"id": 1, "symbol": symbol, "contract_value": "0.001",
                                "settlement_time": (datetime.now(UTC) + timedelta(days=1)).isoformat()}}
    client.metadata_at = time.monotonic()
    try:
        rows = await client.selected([symbol])
        assert rows[0]["bestAsk"] == 10
        assert rows[0]["depth"] == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_watchlist_authenticated_and_asset_scoped(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from app.main import app, settings
    store = EvidenceStore(str(tmp_path / "e.sqlite"), "BTC")
    store.initialize()
    monkeypatch.setattr(settings, "analysis_service_secret", "a-private-service-secret")
    monkeypatch.setattr(app.state, "feed", SimpleNamespace(evidence=SimpleNamespace(store=store)), raising=False)
    expiry = (datetime.now(UTC) + timedelta(days=40)).isoformat()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/market/btcusd/watch-expiries", json={"expiries": [expiry]})).status_code == 401
        response = await client.post("/api/market/btcusd/watch-expiries", json={"expiries": [expiry]},
                                     headers={"X-Analysis-Secret": settings.analysis_service_secret})
        assert response.status_code == 200
        assert len(store.watched(int(time.time() * 1000))) == 1
