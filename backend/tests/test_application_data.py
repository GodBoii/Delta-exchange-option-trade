import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.application_data import ConvexApplicationData
from app.capital import CapitalPolicy
from app.engine import TradingEngine
from app.errors import AppError
from automation_agent.tools import AutomationStrategyTools


async def test_library_reads_all_pages_without_using_supabase():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        args = body["args"]
        if args["defaults"]:
            value = {"page": [], "isDone": True}
        else:
            cursor = args["paginationOpts"]["cursor"]
            value = {
                "page": [{"id": cursor or "first", "definitionJson": '{"name":"Saved"}'}],
                "isDone": cursor is not None,
                "continueCursor": "second",
            }
        return httpx.Response(200, json={"status": "success", "value": value})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = ConvexApplicationData("https://library.test", "secret", client)
        rows = await store.saved_strategies("owner")
    assert [row["id"] for row in rows] == ["first", "second"]
    assert rows[0]["definition_json"] == {"name": "Saved"}
    assert all(request["args"]["userId"] == "owner" for request in requests)


async def test_library_error_does_not_return_an_empty_success():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        store = ConvexApplicationData("https://library.test", "secret", client)
        with pytest.raises(AppError):
            await store.saved_strategies("owner")


async def test_capital_policy_uses_the_migrated_domain():
    engine = TradingEngine(None, None)
    engine.application_data = SimpleNamespace(
        request=AsyncMock(
            return_value={
                "allocation_mode": "fixed_amount",
                "capital_amount": "12.50",
            }
        )
    )
    policy = await engine.capital_policy("owner")
    assert isinstance(policy, CapitalPolicy)
    assert str(policy.capital_amount) == "12.50"
    await engine.save_capital_policy("owner", "half_balance", None)
    engine.application_data.request.assert_awaited_with(
        "library:setCapital",
        {
            "value": {"user_id": "owner", "allocation_mode": "half_balance", "capital_amount": None},
        },
        mutation=True,
    )


def test_ai_library_reads_convex_and_keeps_slot_checks_in_existing_transaction():
    settings = SimpleNamespace(require_database_url=lambda: "postgresql://unused", convex_library_enabled=False)
    tools = AutomationStrategyTools(
        settings,
        user_id="11111111-1111-4111-8111-111111111111",
        agent_run_id="22222222-2222-4222-8222-222222222222",
        market_snapshot_id="33333333-3333-4333-8333-333333333333",
    )
    tools.application_data = SimpleNamespace(
        selection_context=lambda user: (
            [
                {
                    "id": "strategy",
                    "name": "Saved",
                    "version": 9,
                    "definition_json": {},
                    "enabled_for_ai": True,
                }
            ],
            {"allocation_mode": "half_balance"},
        )
    )
    queries = []
    cursor = SimpleNamespace(execute=lambda query, args: queries.append(query), fetchone=lambda: {"count": 0})
    tools._connect = lambda: nullcontext(SimpleNamespace(cursor=lambda: nullcontext(cursor)))
    result = json.loads(tools.show_available_strategy())
    assert result["strategies"][0]["version"] == 9
    assert len(queries) == 1 and "strategy_capital_slots" in queries[0]


async def test_restart_recovery_does_not_steal_an_inflight_local_operation():
    database = SimpleNamespace(update=AsyncMock())
    engine = TradingEngine(database, SimpleNamespace(convex_order_journal_enabled=True))
    engine.strategy_pages = AsyncMock(
        return_value=[
            {"id": "running", "risk_state": {"exclusiveFillAccounting": True}},
            {"id": "interrupted", "risk_state": {"exclusiveFillAccounting": True}},
        ]
    )
    engine.running_operations.add("running")
    await engine.recover_interrupted_states()
    database.update.assert_awaited_once()
    assert database.update.call_args.args[2]["id"] == "eq.interrupted"
    assert database.update.call_args.args[1]["risk_state"]["exitRequested"] is True
