from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.automation import AutomationScheduler, build_account_context
from app.capital import CapitalPolicy, capital_budget
from app.default_strategies import default_strategy_definitions
from app.engine import TradingEngine
from app.shared_analysis import SHARED_USER_ID


async def test_three_accounts_share_strategy_but_size_from_their_own_policy():
    definition = default_strategy_definitions(datetime(2026, 8, 25, tzinfo=UTC))[0]
    engine = TradingEngine(SimpleNamespace(), SimpleNamespace())
    engine.product_spec = AsyncMock(return_value={"contract_value": 1, "initial_margin": 0})
    accounts = [
        (Decimal("2000"), CapitalPolicy("half_balance"), Decimal("1000"), 9),
        (Decimal("10000"), CapitalPolicy("one_quarter_balance"), Decimal("2500"), 24),
        (Decimal("50000"), CapitalPolicy("fixed_amount", Decimal("2000")), Decimal("2000"), 19),
    ]
    for balance, policy, expected_budget, expected_lots in accounts:
        resolved = [{"productSymbol": "C-BTC", "markPrice": "100", "position": "buy", "optionType": "call"}]
        sized = await engine.apply_automatic_lots(object(), definition, resolved, policy, (balance, balance))
        assert capital_budget(balance, balance, policy.allocation_mode, policy.capital_amount) == expected_budget
        assert sized[0]["lots"] == expected_lots
    assert definition.lotsMode == "auto"


async def test_shared_analysis_context_does_not_read_any_account_balance():
    context = await build_account_context(object(), SHARED_USER_ID)
    assert context["scope"] == "shared_market_analysis"
    assert "activeStrategies" not in context


async def test_reservation_uses_the_same_wallet_and_policy_as_sizing():
    db = SimpleNamespace(
        runtime=object(), rpc=AsyncMock(return_value={"slot": 1, "created": True, "occupiedBefore": 0})
    )
    engine = TradingEngine(db, SimpleNamespace())
    engine.client_for_user = AsyncMock(side_effect=AssertionError("Do not fetch a second account snapshot"))
    await engine.reserve_capital_slot(
        "a", "strategy", 4, wallet=(Decimal("100"), Decimal("200")), policy=CapitalPolicy("one_quarter_balance")
    )
    assert db.rpc.call_args.args[1]["p_budget"] == "50"
    assert db.rpc.call_args.args[1]["p_total_balance"] == "200"


async def test_three_users_produce_one_fixed_review_per_session():
    db = SimpleNamespace(
        settings=SimpleNamespace(convex_runtime_enabled=True, shared_analysis_enabled=True),
        select=AsyncMock(return_value=[{"user_id": SHARED_USER_ID, "enabled": True}]),
        upsert=AsyncMock(),
        rpc=AsyncMock(),
    )
    await AutomationScheduler(db, object())._enqueue_session_reviews()
    runs = db.rpc.call_args_list[0].args[1]["p_runs"]
    assert runs
    assert {run["user_id"] for run in runs} == {SHARED_USER_ID}
    assert len({run["run_key"] for run in runs}) == len(runs)
    db.select.assert_awaited_once()
    assert db.select.call_args.args[1]["user_id"] == f"eq.{SHARED_USER_ID}"


@pytest.mark.parametrize(
    "status,outcome,expected",
    [
        ("completed", "strategy_reconfirmed", "ready"),
        ("running", "strategy_reconfirmed", "pending"),
        ("completed", "strategy_dropped", "dropped"),
        ("failed", None, "failed"),
    ],
)
async def test_shared_recheck_controls_every_linked_account(status, outcome, expected):
    db = SimpleNamespace(
        select=AsyncMock(
            side_effect=[
                [{"id": f"proposal-{u}", "strategy_id": u, "shared_decision_id": "decision"} for u in ("a", "b", "c")],
                [{"strategy_proposal_id": "decision", "status": status, "outcome": outcome}],
            ]
        )
    )
    engine = TradingEngine(db, SimpleNamespace())
    assert await engine.activation_recheck_states(["a", "b", "c"]) == dict.fromkeys(["a", "b", "c"], expected)


async def test_convex_recheck_states_use_one_request_for_due_accounts():
    strategy_ids = [f"strategy-{index}" for index in range(100)]
    expected = dict.fromkeys(strategy_ids, "ready")
    request = AsyncMock(return_value=expected)
    db = SimpleNamespace(runtime=SimpleNamespace(data=SimpleNamespace(request=request)))
    engine = TradingEngine(db, SimpleNamespace())
    assert await engine.activation_recheck_states(strategy_ids) == expected
    request.assert_awaited_once_with("runtimeControl:recheckStates", {"strategyIds": strategy_ids})


async def test_shared_allocation_drains_multiple_account_pages():
    pending_cursors = []
    allocations = []

    async def request(path, args, *, mutation=False):
        if path == "sharedAnalysis:allocate":
            assert mutation
            allocations.append(args["userId"])
            return {"reused": False}
        if path == "sharedAnalysis:completeAllocation":
            assert mutation
            assert args["decisionId"] == "common"
            return None
        assert path == "sharedAnalysis:pendingAllocationPage"
        pending_cursors.append(args["cursor"])
        page = len(pending_cursors) - 1
        count = 50 if page == 2 else 100
        return {
            "items": [{"decisionId": "common", "userId": f"user-{page * 100 + index}"} for index in range(count)],
            "decisions": ["common"],
            "continueCursor": str(page + 1),
            "isDone": page == 2,
        }

    db = SimpleNamespace(
        settings=SimpleNamespace(shared_allocation_concurrency=8),
        runtime=SimpleNamespace(data=SimpleNamespace(request=AsyncMock(side_effect=request))),
    )
    await AutomationScheduler(db, object())._allocate_shared_decisions()
    assert pending_cursors == [None, "1", "2"]
    assert len(allocations) == len(set(allocations)) == 250
    assert db.runtime.data.request.await_args.args[0] == "sharedAnalysis:completeAllocation"
