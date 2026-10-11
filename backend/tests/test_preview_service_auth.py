import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.errors import MARKET_AUTH_ERROR_CODE, MARKET_AUTH_ERROR_MESSAGE
from news_analyzer import main


@pytest.mark.parametrize("outcome", [None, "strategy_selected", "wait_and_run_again"])
def test_auth_failure_cannot_be_reported_as_no_trade_or_undo_a_committed_action(monkeypatch, outcome):
    result = SimpleNamespace(
        run_id="run", session_id="session", model_id="model", report="No trade",
        market_snapshot_id="snapshot", member_responses=[],
        tool_calls=[{"name": "calculate_exit_time", "result": json.dumps({"errorCode": MARKET_AUTH_ERROR_CODE})}],
    )
    monkeypatch.setattr(main, "run_automation_team", lambda **_: result)
    monkeypatch.setattr(main, "read_automation_state", lambda *_, **__: {"outcome": outcome})
    body = main.AutomationAnalysisRequest(
        userId="global", agentRunId=str(uuid4()), sessionId="review", accountContext={}, trigger="asia_session",
    )
    if outcome:
        assert main._run_automation_analysis(body, "trace")["outcome"] == outcome
    else:
        with pytest.raises(main.ServiceError) as raised:
            main._run_automation_analysis(body, "trace")
        assert raised.value.code == MARKET_AUTH_ERROR_CODE
        assert raised.value.message == MARKET_AUTH_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_worker_boundary_keeps_safe_authentication_error(monkeypatch):
    def worker(*_, **__):
        raise RuntimeError(MARKET_AUTH_ERROR_MESSAGE)

    monkeypatch.setattr(main, "run_in_worker", worker)
    monkeypatch.setattr(main, "settings", SimpleNamespace(openrouter_api_key="test", database_url="test"))
    with pytest.raises(main.ServiceError) as raised:
        await main.analyze_automation(
            SimpleNamespace(trigger="asia_session"), SimpleNamespace(state=SimpleNamespace(trace_id="trace")),
        )
    assert raised.value.code == MARKET_AUTH_ERROR_CODE
    assert raised.value.status == 503
