import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_curated_input import fixture

from app.errors import MARKET_AUTH_ERROR_CODE, MARKET_AUTH_ERROR_MESSAGE
from automation_agent.tools import AutomationStrategyTools
from news_analyzer import main


@pytest.mark.parametrize("status", [200, 401, 403, 503])
def test_preview_requires_authenticated_watch_and_preserves_failure_reason(monkeypatch, status):
    raw, definition, _ = fixture()
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("option-catalogue"):
            return httpx.Response(200, json={"underlying": "BTC", "options": [raw]})
        if request.url.path.endswith("selected-contracts"):
            return httpx.Response(200, json={"asset": "BTC", "contracts": [raw]})
        assert request.url.path.endswith("watch-expiries")
        assert request.headers["X-Analysis-Secret"] == "private-service-secret"
        return httpx.Response(status, json={"success": status == 200})

    client_type = httpx.Client
    monkeypatch.setattr(
        "automation_agent.tools.httpx.Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    tool = object.__new__(AutomationStrategyTools)
    tool.asset = "BTC"
    tool.agent_run_id = str(uuid4())
    tool.user_id = "global"
    tool.settings = SimpleNamespace(analysis_service_secret="private-service-secret")
    tool._previews = {}
    tool.strategy_references = {"S01": ("saved-id", 1)}
    tool.application_data = SimpleNamespace(selection_context=lambda *_: ([{
        "enabled_for_ai": True, "version": 1, "definition_json": definition,
    }], {}))
    now = datetime.now(UTC)
    result = json.loads(tool.preview_strategy(
        "S01", (now + timedelta(minutes=10)).isoformat(),
        {"kind": "specific_time", "exit_at": (now + timedelta(hours=1)).isoformat()},
    ))
    assert len(requests) == 3
    assert result["valid"] is (status == 200)
    assert bool(tool._previews) is (status == 200)
    if status in {401, 403}:
        assert result["errorCode"] == MARKET_AUTH_ERROR_CODE
        assert result["reason"] == MARKET_AUTH_ERROR_MESSAGE
    elif status == 503:
        assert result["errorCode"] == "market_service_unavailable"
    assert "private-service-secret" not in json.dumps(result)


@pytest.mark.parametrize("outcome", [None, "strategy_selected", "wait_and_run_again"])
def test_auth_failure_cannot_be_reported_as_no_trade_or_undo_a_committed_action(monkeypatch, outcome):
    result = SimpleNamespace(
        run_id="run", session_id="session", model_id="model", report="No trade",
        market_snapshot_id="snapshot", member_responses=[],
        tool_calls=[{"name": "preview_strategy", "result": json.dumps({"errorCode": MARKET_AUTH_ERROR_CODE})}],
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
