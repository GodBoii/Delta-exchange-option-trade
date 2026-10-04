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
