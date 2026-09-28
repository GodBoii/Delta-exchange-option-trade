from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import main
from app.default_strategies import default_strategy_definitions
from app.errors import AppError
from app.exit_schedule import ExitChoice, template_from_definition

ENTRY = datetime(2026, 9, 29, 13, tzinfo=UTC)
EXPIRY = datetime(2026, 9, 30, 12, tzinfo=UTC)
TEMPLATE = template_from_definition(default_strategy_definitions(ENTRY)[4].model_dump(mode="json", exclude_none=True))


class CatalogClient:
    async def request(self, method, path, *, query=None):
        assert method == "GET"
        if path == "/v2/tickers":
            assert query["underlying_asset_symbols"] == "BTC"
            return {
                "result": [
                    {"symbol": f"{kind}-BTC-{strike}-300926", "strike_price": str(strike), "spot_price": "84000"}
                    for kind in ("C", "P")
                    for strike in range(83200, 85000, 200)
                ]
            }
        return {"result": {"settlement_time": EXPIRY.isoformat()}}

    async def close(self):
        pass


class Engine:
    def __init__(self):
        self.version = 4
        self.saved = []

    async def saved_strategies(self, user_id, strategy_id):
        assert user_id == "user-1" and strategy_id == "strategy-1"
        return [{"version": self.version, "definition_json": TEMPLATE}]

    async def save_strategy(self, user_id, definition, status, saved_id):
        self.saved.append((definition, status, saved_id))
        return {"id": "run-1", "status": "scheduled"}


@pytest.mark.asyncio
async def test_manual_preview_and_commit_use_the_same_resolved_contract(monkeypatch):
    engine = Engine()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine, db=object())))
    monkeypatch.setattr(main, "delta_client_for_user", lambda *_args: _client())
    monkeypatch.setattr(main, "current_account", lambda *_args, **_kwargs: _account())
    body = main.ExitScheduleRequest(
        savedStrategyId="strategy-1",
        expectedVersion=4,
        entryAt=ENTRY,
        exitChoice=ExitChoice(kind="intraday", hours=7),
    )
    preview = await main.preview_exit_schedule(request, body, {"id": "user-1"})
    schedule = preview["result"]["schedule"]
    committed = await main.schedule_with_exit_choice(
        request,
        main.CommitExitScheduleRequest(
            **body.model_dump(),
            expectedExitUtc=datetime.fromisoformat(schedule["exitUtc"]),
            expectedContractExpiryUtc=datetime.fromisoformat(schedule["contractExpiryUtc"]),
        ),
        {"id": "user-1"},
    )
    assert committed["result"]["schedule"] == schedule
    assert engine.saved[0][0].legs[0].expiry == EXPIRY.date()
    assert engine.saved[0][1:] == ("scheduled", "strategy-1")

    engine.version = 5
    with pytest.raises(AppError, match="version changed"):
        await main.resolve_saved_schedule(request, "user-1", body)


@pytest.mark.asyncio
async def test_manual_commit_rejects_stale_preview(monkeypatch):
    engine = Engine()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine, db=object())))
    monkeypatch.setattr(main, "delta_client_for_user", lambda *_args: _client())
    monkeypatch.setattr(main, "current_account", lambda *_args, **_kwargs: _account())
    body = main.CommitExitScheduleRequest(
        savedStrategyId="strategy-1",
        expectedVersion=4,
        entryAt=ENTRY,
        exitChoice=ExitChoice(kind="intraday", hours=7),
        expectedExitUtc=ENTRY + timedelta(hours=8),
        expectedContractExpiryUtc=EXPIRY,
    )
    with pytest.raises(AppError, match="preview again"):
        await main.schedule_with_exit_choice(request, body, {"id": "user-1"})
    assert not engine.saved


async def _client():
    return CatalogClient()


async def _account():
    return {"id": "user-1"}
