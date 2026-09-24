import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.errors import AppError
from app.runtime_store import ConvexRuntimeStore, conditions


def test_filters_do_not_turn_missing_values_into_zero_or_loose_matches():
    result = conditions({"status": "eq.active", "exit_execution_at": "is.null", "id": "in.(a,b)", "limit": "1"})
    assert result[1] == {"field": "exit_execution_at", "op": "is", "valueJson": "null"}
    assert json.loads(result[2]["valueJson"]) == ["a", "b"]
    with pytest.raises(AppError):
        conditions({"or": "(status.eq.active,status.eq.attention)"})


async def test_status_claim_goes_directly_to_transaction_and_keeps_predicate():
    data = SimpleNamespace(request=AsyncMock(return_value=['{"id":"run","status":"executing_exit"}']))
    result = await ConvexRuntimeStore(data).update(
        "strategies", {"status": "executing_exit"}, {"id": "eq.run", "status": "eq.active"}
    )
    assert result[0]["status"] == "executing_exit"
    data.request.assert_awaited_once()
    assert data.request.call_args.args[0] == "runtimeRecords:update"
    assert {"field": "status", "op": "eq", "valueJson": '"active"'} in data.request.call_args.args[1]["conditions"]


async def test_projection_happens_after_all_pages_and_ordering():
    data = SimpleNamespace(
        request=AsyncMock(
            side_effect=[
                {"page": ['{"id":"a","created_at":"2026-09-01"}'], "isDone": False, "continueCursor": "next"},
                {"page": ['{"id":"b","created_at":"2026-09-02"}'], "isDone": True},
            ]
        )
    )
    result = await ConvexRuntimeStore(data).select(
        "strategies", {"select": "id", "order": "created_at.desc", "limit": "1"}
    )
    assert result == [{"id": "b"}]


async def test_settings_update_preserves_existing_limits_in_user_record():
    data = SimpleNamespace(request=AsyncMock(side_effect=[
        {"user_id": "owner", "enabled": True, "model_id": "model",
         "minimum_follow_up_minutes": 5, "maximum_agent_runs_per_day": 2},
        {"user_id": "owner", "enabled": False, "maximum_agent_runs_per_day": 2},
    ]))
    await ConvexRuntimeStore(data).write("automation_settings", {"user_id": "owner", "enabled": False}, "user_id")
    assert data.request.call_args.args[0] == "settings:saveAutomation"
    value = data.request.call_args.args[1]["value"]
    assert value["maximum_agent_runs_per_day"] == 2
    assert value["enabled"] is False


async def test_enabled_settings_paginate_beyond_the_first_page():
    pages = [
        {"page": [{"user_id": f"user-{i}", "enabled": True} for i in range(start, start + 100)],
         "isDone": False, "continueCursor": str(start + 100)}
        for start in (0, 100)
    ]
    pages.append({"page": [{"user_id": f"user-{i}", "enabled": True} for i in range(200, 250)],
                  "isDone": True, "continueCursor": ""})
    request = AsyncMock(side_effect=pages)
    rows = await ConvexRuntimeStore(SimpleNamespace(request=request)).select(
        "automation_settings", {"select": "user_id", "enabled": "eq.true", "user_id": "neq.global"}
    )
    assert len(rows) == 250
    assert request.await_count == 3
    assert all(call.args[1]["enabled"] is True for call in request.await_args_list)
