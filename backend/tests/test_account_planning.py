"""Account planning is read-only and selection must consider actual funding."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.account_manager import AccountManager
from app.capital import CapitalPolicy, has_available_capital_slot, policy_budget, policy_from_row, requested_budget
from app.errors import AppError
from app.models import CapitalSettingsUpdate


@pytest.fixture
def planner():
    manager = object.__new__(AccountManager)
    rows = [
        dict(
            account_id=str(number),
            account_name=name,
            parent_id="100",
            present=True,
            connected=True,
            participation="automatic",
            busy=False,
        )
        for number, name in [(101, "Empty"), (102, "Funded")]
    ]
    assigned = {"account_id": "102", "strategy_id": "run", "parent_id": "100", "budget": None}
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value=None))))

    @asynccontextmanager
    async def connect():
        yield connection

    manager.registry = SimpleNamespace(
        assignment=AsyncMock(return_value=None),
        settings=AsyncMock(return_value={"mode": "automatic", "auto_fund": False}),
        main=AsyncMock(return_value={"delta_user_id": "100", "account_name": "Main"}),
        accounts=AsyncMock(return_value=rows),
        reserve=AsyncMock(return_value=assigned),
        pool=SimpleNamespace(connection=connect),
    )

    async def flat(_owner, account):
        amount = Decimal(0 if account == "101" else 250)
        return amount, amount

    manager.funding = SimpleNamespace(
        flat=AsyncMock(side_effect=flat), source_available=AsyncMock(return_value=Decimal(1000))
    )
    manager.wallet = AsyncMock(return_value=(Decimal(1000), Decimal(1000)))
    manager.engine = SimpleNamespace(capital_policy=AsyncMock(return_value=CapitalPolicy("fixed_amount", Decimal(250))))
    return manager


async def test_automatic_assignment_skips_empty_account_when_auto_funding_is_off(planner):
    await planner.choose({"user_id": "owner", "id": "run"})
    planner.registry.reserve.assert_awaited_once_with("owner", "run", "102")


async def test_auto_funding_still_prefers_already_funded_accounts(planner):
    planner.registry.settings.return_value["auto_fund"] = True
    await planner.choose({"user_id": "owner", "id": "run"})
    planner.registry.reserve.assert_awaited_once_with("owner", "run", "102")


async def test_no_fundable_account_is_not_permanently_reserved(planner):
    planner.funding.flat.return_value = (Decimal(0), Decimal(0))
    planner.funding.flat.side_effect = None
    with pytest.raises(AppError, match="No eligible"):
        await planner.choose({"user_id": "owner", "id": "run"})
    planner.registry.reserve.assert_not_awaited()


async def test_preview_uses_same_candidate_without_reserving_or_transferring(planner):
    preview = await planner.preview("owner", Decimal(250))
    assert preview["account"]["id"] == "102" and preview["transfers"] == []
    planner.registry.reserve.assert_not_awaited()


async def test_preview_lists_funding_transfers_without_dispatch(planner):
    planner.registry.settings.return_value["auto_fund"] = True
    planner.funding.flat.side_effect = None
    planner.funding.flat.return_value = (Decimal(0), Decimal(0))
    preview = await planner.preview("owner", Decimal(250))
    assert preview["transfers"] == [
        {"source": "Main", "target": "Empty", "amount": "250", "purpose": "Fund the strategy"}
    ]
    planner.registry.reserve.assert_not_awaited()


async def test_pending_transfer_blocks_preview(planner):
    async with planner.registry.pool.connection() as connection:
        connection.execute.return_value.fetchone.return_value = (1,)
    preview = await planner.preview("owner", Decimal(250))
    assert preview["status"] == "blocked" and preview["account"] is None
    planner.funding.flat.assert_not_awaited()


@pytest.mark.parametrize(
    "mode,extra",
    [
        ("fixed_amount", {"capital_amount": Decimal(250)}),
        ("half_balance", {}),
        ("custom_percentage", {"allocation_percent": Decimal(25)}),
    ],
)
def test_wait_for_full_budget_blocks_shortage(mode, extra):
    policy = CapitalPolicy(mode, allow_smaller_entries=False, **extra)
    assert policy_budget(Decimal(80), Decimal(1000), policy) == 0
    assert not has_available_capital_slot(Decimal(80), Decimal(1000), policy, [], Decimal(0))
    assert requested_budget(Decimal(1000), policy) >= 250


def test_fixed_budget_above_total_is_not_silently_shrunk_in_wait_mode():
    policy = CapitalPolicy("fixed_amount", Decimal(250), allow_smaller_entries=False)
    assert policy_budget(Decimal(80), Decimal(80), policy) == 0
    assert requested_budget(Decimal(80), policy) == 250


def test_shortage_policy_roundtrips_and_legacy_updates_preserve_it():
    policy = policy_from_row({"allocationMode": "half_balance", "allowSmallerEntries": False})
    assert policy.as_json()["allowSmallerEntries"] is False
    assert CapitalSettingsUpdate(allowSmallerEntries=False).extensions() == {"allow_smaller_entries": False}
    assert "allow_smaller_entries" not in CapitalSettingsUpdate(allocationMode="half_balance").extensions()
    assert policy_budget(Decimal(80), Decimal(1000), CapitalPolicy("fixed_amount", Decimal(250))) == 80


async def test_automation_capacity_rejects_an_unfundable_account_before_analysis():
    from app.engine import TradingEngine

    db = SimpleNamespace(
        rpc=AsyncMock(
            return_value={
                "connected": True,
                "occupiedSlots": [],
                "reservedBudget": "0",
                "reservedByAsset": {},
            }
        )
    )
    engine = TradingEngine(db, SimpleNamespace())
    engine.capital_policy = AsyncMock(return_value=CapitalPolicy("fixed_amount", Decimal(250)))
    engine.subaccounts = SimpleNamespace(
        available=AsyncMock(return_value=True),
        preview=AsyncMock(return_value={"status": "blocked"}),
    )
    engine.managed_wallet = AsyncMock(return_value=(Decimal(1000), Decimal(1000)))
    engine.client_for_user = AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    try:
        assert not await engine.has_available_trade_slot("owner", "BTC")
        engine.subaccounts.preview.assert_awaited_once_with("owner", Decimal(250))
    finally:
        await engine.close()


async def test_preview_api_is_authenticated_read_only_and_uses_proposed_policy(monkeypatch):
    import httpx

    from app import main
    from app.auth import require_user

    db = SimpleNamespace(
        rpc=AsyncMock(
            return_value={
                "occupiedSlots": [],
                "reservedBudget": "0",
                "reservedByAsset": {},
            }
        )
    )
    client = SimpleNamespace(close=AsyncMock())
    engine = SimpleNamespace(
        capital_policy=AsyncMock(return_value=CapitalPolicy()),
        subaccounts=None,
        client_for_user=AsyncMock(return_value=client),
        managed_wallet=AsyncMock(return_value=None),
        usd_capital=AsyncMock(return_value=(Decimal(80), Decimal(1000))),
        save_capital_policy=AsyncMock(),
    )
    monkeypatch.setattr(main.app.state, "engine", engine, raising=False)
    monkeypatch.setattr(main.app.state, "db", db, raising=False)
    monkeypatch.setattr(main, "current_account", AsyncMock(return_value={"id": "100"}))
    transport = httpx.ASGITransport(app=main.app)
    main.app.dependency_overrides[require_user] = lambda: {"id": "owner"}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as api:
            response = await api.post(
                "/api/capital/preview",
                json={
                    "allocationMode": "fixed_amount",
                    "capitalAmount": 250,
                    "allowSmallerEntries": False,
                    "asset": "ETH",
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["assetBudgets"]["ETH"]["nominalBudgetPerStrategy"] == 250
            assert result["assetBudgets"]["ETH"]["availableBudgetForNextStrategy"] == 0
            assert result["entryPreview"]["status"] == "blocked"
            assert result["walletScope"] == "main"
            engine.save_capital_policy.assert_not_awaited()
            db.rpc.assert_awaited_once_with("get_strategy_capital_capacity", {"p_user_id": "owner"})
            invalid = await api.post(
                "/api/capital/preview", json={"allocationMode": "custom_percentage", "allocationPercent": 101}
            )
            assert invalid.status_code == 400
            main.app.dependency_overrides.pop(require_user)
            unauthenticated = await api.post("/api/capital/preview", json={})
            assert unauthenticated.status_code == 401
    finally:
        main.app.dependency_overrides.pop(require_user, None)
