import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agno.agent import Agent
from agno.exceptions import ModelProviderError
from agno.models.message import Message
from openai._base_client import BaseClient

from news_agent.agent import _create_model


def news_model():
    return _create_model(
        SimpleNamespace(model_id="deepseek/deepseek-v4.1-flash-1017", openrouter_api_key="test-key"), False
    )


def completion(content="Verified news", *, tool_calls=None):
    return httpx.Response(200, json={
        "id": "test-completion", "object": "chat.completion", "created": 1,
        "model": "deepseek/deepseek-v4.1-flash-1017",
        "choices": [{"index": 0, "finish_reason": "tool_calls" if tool_calls else "stop",
                     "message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}],
    })


def provider_error(status, *, embedded=False, headers=None):
    return httpx.Response(200 if embedded else status, headers=headers, json={
        "error": {"code": status, "message": "The operation was aborted sk-test-secret"},
    })


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    # Keep the real SDK retry loop and transport, omitting only the wait in tests.
    monkeypatch.setattr(BaseClient, "_calculate_retry_timeout", lambda *_, **__: 0)


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("injected_client", [False, True])
@pytest.mark.asyncio
async def test_news_request_recovers_on_third_retry(monkeypatch, embedded, injected_client):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return provider_error(502, embedded=embedded) if len(requests) <= 3 else completion()

    model = news_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if injected_client:
            model.http_client = client
        else:
            monkeypatch.setattr("news_agent.failures.DefaultAsyncHttpxClient", lambda: client)
        result = await model.ainvoke([Message(role="user", content="Research news")], Message(role="assistant"))
    assert result.content == "Verified news"
    assert len(requests) == 4
    assert all(request == requests[0] for request in requests)
    assert model.last_failure is None


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.asyncio
async def test_retry_exhaustion_preserves_redacted_failure(embedded):
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        # Mixing HTTP and embedded errors must not multiply the retry budget.
        return provider_error(502, embedded=embedded if attempts % 2 else not embedded)

    model = news_model()
    model.api_key = "sk-test-secret"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.http_client = client
        with pytest.raises(ModelProviderError):
            await model.ainvoke([Message(role="user", content="Research news")], Message(role="assistant"))
    assert attempts == 4
    assert model.last_failure == {
        "code": "model_provider_failed", "stage": "news", "http_status": 502,
        "message": "The operation was aborted [redacted]",
    }


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("status", [400, 401, 402, 403, 422])
@pytest.mark.asyncio
async def test_permanent_errors_are_not_retried(embedded, status):
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        return provider_error(status, embedded=embedded)

    model = news_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.http_client = client
        with pytest.raises(ModelProviderError):
            await model.ainvoke([Message(role="user", content="Research news")], Message(role="assistant"))
    assert attempts == 1
    assert model.last_failure["http_status"] == status


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.asyncio
async def test_retries_do_not_repeat_completed_research_tools(monkeypatch, embedded):
    monkeypatch.setenv("AGNO_TELEMETRY", "false")
    tool_calls = 0
    requests = []

    def collect_evidence() -> str:
        """Read current news evidence."""
        nonlocal tool_calls
        tool_calls += 1
        return "Existing source evidence"

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return completion(None, tool_calls=[{
                "id": "research-1", "type": "function",
                "function": {"name": "collect_evidence", "arguments": "{}"},
            }])
        return provider_error(502, embedded=embedded) if len(requests) <= 4 else completion()

    model = news_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.http_client = client
        result = await Agent(model=model, tools=[collect_evidence], telemetry=False).arun("Research news")
        assert len(client.event_hooks["response"]) == 1
    assert result.content == "Verified news"
    assert tool_calls == 1
    assert len(requests) == 5
    assert all(request == requests[1] for request in requests[2:])
    assert any(message["role"] == "tool" for message in requests[1]["messages"])


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.asyncio
async def test_retry_after_reaches_sdk_backoff(monkeypatch, embedded):
    monkeypatch.undo()
    sleep = AsyncMock()
    monkeypatch.setattr("openai._base_client.anyio.sleep", sleep)
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return provider_error(429, embedded=embedded, headers={"Retry-After": "2"})
        return completion()

    model = news_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.http_client = client
        await model.ainvoke([Message(role="user", content="Research news")], Message(role="assistant"))
    sleep.assert_awaited_once_with(2)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_connection_failures_share_the_retry_budget(error_type):
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise error_type("Connection lost", request=request)
        return completion()

    model = news_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.http_client = client
        result = await model.ainvoke([Message(role="user", content="Research news")], Message(role="assistant"))
    assert result.content == "Verified news"
    assert attempts == 4


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.asyncio
async def test_provider_can_explicitly_disable_retries(embedded):
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        return provider_error(502, embedded=embedded, headers={"x-should-retry": "false"})

    model = news_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.http_client = client
        with pytest.raises(ModelProviderError):
            await model.ainvoke([Message(role="user", content="Research news")], Message(role="assistant"))
    assert attempts == 1


@pytest.mark.parametrize("error", [None, "provider error", {"code": "502"}, {"code": True}, {"code": 999}])
@pytest.mark.asyncio
async def test_invalid_error_envelopes_do_not_trigger_retries(error):
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"error": error})

    model = news_model()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model.http_client = client
        with pytest.raises(ModelProviderError):
            await model.ainvoke([Message(role="user", content="Research news")], Message(role="assistant"))
    assert attempts == 1
