"""Personal asset-filter request contracts without an external database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.auth import require_user
from app.reporting_api import router


@pytest.fixture
async def api():
    ledger = SimpleNamespace(
        summary=AsyncMock(return_value={"netRealizedPnl": "0"}),
        trades=AsyncMock(return_value={"items": [], "nextCursor": None}),
        history_status=AsyncMock(return_value={"complete": True, "verifiedAt": None}),
        daily_pnl=AsyncMock(return_value=[]),
        strategy_names=AsyncMock(return_value=["ETH Short ATM straddle", "Short ATM straddle"]),
    )
    app = FastAPI()
    app.include_router(router)
    app.state.db = SimpleNamespace(runtime=SimpleNamespace(ledger=ledger))
    app.dependency_overrides[require_user] = lambda: {"id": "account"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, ledger


@pytest.mark.parametrize("asset", [None, "BTC", "ETH"])
async def test_personal_totals_and_chart_pages_forward_the_same_asset_scope(api, asset):
    client, ledger = api
    params = {"range": "all"}
    if asset:
        params["asset"] = asset
    summary = await client.get("/api/me/pnl", params=params)
    assert summary.status_code == 200
    assert summary.json()["asset"] == (asset or "all")
    ledger.summary.assert_awaited_once_with("account", deleted="exclude", since=None, asset=asset)

    trades = await client.get(
        "/api/me/trades", params={**params, "state": "settled", "cursor": "next-page", "limit": 50}
    )
    assert trades.status_code == 200
    assert trades.json()["asset"] == (asset or "all")
    ledger.trades.assert_awaited_once_with(
        "account", deleted="exclude", since=None, asset=asset, state="settled", cursor="next-page", limit=50
    )


@pytest.mark.parametrize("path", ["/api/me/pnl", "/api/me/trades"])
@pytest.mark.parametrize("asset", ["SOL", "btc", ""])
async def test_unsupported_asset_is_rejected_before_the_ledger(api, path, asset):
    client, ledger = api
    response = await client.get(path, params={"asset": asset})
    assert response.status_code == 422
    ledger.summary.assert_not_awaited()
    ledger.trades.assert_not_awaited()


async def test_strategy_scope_is_forwarded_and_echoed_for_every_personal_report(api):
    client, ledger = api
    params = {"range": "all", "asset": "ETH", "strategy": "ETH Short ATM straddle"}
    for path in ("/api/me/pnl", "/api/me/trades", "/api/me/pnl/calendar"):
        response = await client.get(path, params=params)
        assert response.status_code == 200
        assert response.json()["strategy"] == params["strategy"]
        assert response.json()["asset"] == "ETH"
    ledger.summary.assert_awaited_once_with(
        "account", deleted="exclude", since=None, asset="ETH", strategy=params["strategy"]
    )
    ledger.trades.assert_awaited_once_with(
        "account", deleted="exclude", since=None, asset="ETH", strategy=params["strategy"],
        state=None, cursor=None, limit=25,
    )
    ledger.daily_pnl.assert_awaited_once_with("account", since=None, asset="ETH", strategy=params["strategy"])


async def test_calendar_exposes_explicit_ist_bounds_and_history_status(api):
    client, ledger = api
    response = await client.get("/api/me/pnl/calendar", params={"range": "30d"})
    body = response.json()
    assert response.status_code == 200
    assert body["timezone"] == "Asia/Kolkata"
    assert body["startDate"] <= body["endDate"]
    assert body["historyComplete"] is True
    assert body["days"] == []
    assert ledger.daily_pnl.await_args.kwargs["since"] is not None


async def test_strategy_names_are_account_scoped_and_independent_of_pagination(api):
    client, ledger = api
    response = await client.get("/api/me/pnl/strategies")
    assert response.status_code == 200
    assert response.json()["names"] == ["ETH Short ATM straddle", "Short ATM straddle"]
    ledger.strategy_names.assert_awaited_once_with("account")


@pytest.mark.parametrize("path", ["/api/me/pnl", "/api/me/trades", "/api/me/pnl/calendar"])
@pytest.mark.parametrize("strategy", ["", "x" * 501])
async def test_invalid_strategy_filter_is_rejected(api, path, strategy):
    client, ledger = api
    response = await client.get(path, params={"strategy": strategy})
    assert response.status_code == 422
    ledger.summary.assert_not_awaited()
    ledger.trades.assert_not_awaited()
    ledger.daily_pnl.assert_not_awaited()
