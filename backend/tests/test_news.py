from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app import news
from app.errors import AppError


class FakeAsyncClient:
    def __init__(self, response: httpx.Response, captured: dict[str, Any], **_: Any) -> None:
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.captured.update({"method": method, "url": url, **kwargs})
        return self.response


def install_fake_client(monkeypatch, response: httpx.Response, captured: dict[str, Any]) -> None:
    monkeypatch.setattr(
        news.httpx,
        "AsyncClient",
        lambda **kwargs: FakeAsyncClient(response, captured, **kwargs),
    )


def test_news_request_normalizes_query_and_rejects_unknown_fields() -> None:
    request = news.NewsAnalysisRequest(query="  Analyze   recent Bitcoin news  ", sessionId="btc-desk")
    assert request.query == "Analyze recent Bitcoin news"
    with pytest.raises(ValidationError):
        news.NewsAnalysisRequest.model_validate(
            {"query": "Analyze recent Bitcoin news", "sessionId": "btc-desk", "execute": True}
        )


@pytest.mark.asyncio
async def test_get_news_session_proxies_authenticated_user(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    expected = {"success": True, "sessionId": "btc-desk", "history": []}
    install_fake_client(monkeypatch, httpx.Response(200, json=expected), captured)

    response = await news.get_news_session("btc-desk", {"id": "user-9"})

    assert response == expected
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/v1/sessions/btc-desk")
    assert captured["params"] == {"userId": "user-9"}


@pytest.mark.asyncio
async def test_analyze_news_proxies_user_without_exposing_it_in_frontend(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    expected = {"success": True, "sessionId": "btc-desk", "history": []}
    install_fake_client(monkeypatch, httpx.Response(200, json=expected), captured)

    response = await news.analyze_news(
        news.NewsAnalysisRequest(query="Analyze recent Bitcoin news", sessionId="btc-desk"),
        {"id": "user-7"},
    )

    assert response == expected
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/analyze")
    assert captured["json"]["userId"] == "user-7"
    assert captured["json"]["query"] == "Analyze recent Bitcoin news"


@pytest.mark.asyncio
async def test_news_analyzer_error_is_preserved(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    install_fake_client(
        monkeypatch,
        httpx.Response(
            404,
            json={"success": False, "error": {"code": "news_session_not_found", "message": "No output"}},
        ),
        captured,
    )

    with pytest.raises(AppError) as caught:
        await news.get_news_session("btc-desk", {"id": "user-9"})

    assert caught.value.status == 404
    assert caught.value.code == "news_session_not_found"
