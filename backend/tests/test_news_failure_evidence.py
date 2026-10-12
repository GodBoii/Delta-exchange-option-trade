from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agno.exceptions import ModelProviderError

from app import automation
from app.errors import AppError
from news_agent.failures import NewsResearchError, ResearchOpenRouter
from news_analyzer import main
from news_analyzer.worker import AnalysisWorkerError, run_in_worker


def raise_service_credit_error():
    raise main.ServiceError(502, "Key limit exceeded", "model_credit_limit")


def test_worker_preserves_provider_failure_code_across_process_boundary():
    with pytest.raises(AnalysisWorkerError) as caught:
        run_in_worker(raise_service_credit_error, timeout_seconds=10)
    assert caught.value.code == "model_credit_limit"
    assert caught.value.status == 502


@pytest.mark.asyncio
async def test_news_model_retains_redacted_provider_reason(monkeypatch):
    from agno.models.openrouter import OpenRouter

    async def fail(*_, **__):
        raise ModelProviderError(message="Key limit exceeded sk-test-secret", status_code=403)

    monkeypatch.setattr(OpenRouter, "ainvoke", fail)
    model = ResearchOpenRouter(api_key="sk-test-secret")
    with pytest.raises(ModelProviderError):
        await model.ainvoke([], None)
    assert model.last_failure["code"] == "model_credit_limit"
    assert "sk-test-secret" not in model.last_failure["message"]


def test_analyzer_keeps_news_failure_classification(monkeypatch):
    monkeypatch.setattr(
        main,
        "run_automation_team",
        lambda **_: (_ for _ in ()).throw(
            NewsResearchError({"code": "model_credit_limit", "message": "Key limit exceeded"}, [])
        ),
    )
    monkeypatch.setattr(main, "read_automation_state", lambda *_, **__: None)
    body = main.AutomationAnalysisRequest(
        userId="global",
        agentRunId="11111111-1111-4111-8111-111111111111",
        sessionId="test",
        accountContext={},
        trigger="asia_session",
    )
    with pytest.raises(main.ServiceError) as caught:
        main._run_automation_analysis(body, "test")
    assert caught.value.code == "model_credit_limit"


@pytest.mark.asyncio
async def test_credit_failure_is_saved_and_shown_clearly(monkeypatch):
    rows = []
    database = SimpleNamespace(
        update=AsyncMock(side_effect=lambda *args: rows.append(args[1]) or []), select=AsyncMock(return_value=[])
    )
    monkeypatch.setattr(automation, "has_automation_trade_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(automation, "build_account_context", AsyncMock(return_value={}))

    async def post(*_, **__):
        return httpx.Response(502, json={"error": {"code": "model_credit_limit", "message": "Key limit exceeded"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(AppError, match="credit or key limit"):
        await automation.execute_automation_run(
            db=database,
            engine=object(),
            user_id="global",
            run_id="run",
            session_id="test",
            trigger="asia_session",
            reason="test",
        )
    assert rows[-1]["error"] == automation.CREDIT_LIMIT_MESSAGE
    assert automation.public_run_error(rows[-1]["error"]) == automation.CREDIT_LIMIT_MESSAGE
