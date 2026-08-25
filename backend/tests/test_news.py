from types import SimpleNamespace

import pytest

from app import news
from app.errors import AppError

SESSION_ID = "11111111-1111-4111-8111-111111111111"
NEWS_MEMBER = {
    "runId": "news-run-1",
    "agentId": "news-intelligence-analyst",
    "agentName": "News Intelligence Analyst",
    "model": "deepseek/deepseek-v4-flash-vision-exp",
    "content": "## Summary\nBitcoin news analysis.",
    "createdAt": "2026-08-25T00:00:00Z",
    "researchTools": ["search_news", "read_news_article"],
}


class FakeDB:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[dict] = []

    async def select(self, table: str, query: dict) -> list[dict]:
        assert table == "automation_agent_runs"
        self.queries.append(query)
        if query.get("id") and query["id"] != f"eq.{SESSION_ID}":
            return []
        return self.rows


def request_with(rows: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=FakeDB(rows))))


def completed_row(member_responses: list[dict] | None = None) -> dict:
    return {
        "id": SESSION_ID,
        "agno_run_id": "team-run-1",
        "model_id": "deepseek/deepseek-v4-flash-vision-exp",
        "member_responses": member_responses if member_responses is not None else [NEWS_MEMBER],
        "created_at": "2026-08-25T00:00:00Z",
        "updated_at": "2026-08-25T00:05:00Z",
    }


@pytest.mark.asyncio
async def test_lists_news_member_runs_from_main_automation_history() -> None:
    request = request_with([completed_row()])

    response = await news.list_news_sessions(request, {"id": "user-9"})

    assert response["success"] is True
    assert response["sessions"][0]["sessionId"] == SESSION_ID
    assert response["sessions"][0]["runId"] == "news-run-1"
    assert "Bitcoin news analysis" in response["sessions"][0]["preview"]


@pytest.mark.asyncio
async def test_reads_original_news_member_markdown_from_main_run() -> None:
    response = await news.get_news_session(SESSION_ID, request_with([completed_row()]), {"id": "user-9"})

    assert response["analysis"] == NEWS_MEMBER["content"]
    assert response["researchTools"] == NEWS_MEMBER["researchTools"]
    assert response["history"] == []


@pytest.mark.asyncio
async def test_rejects_main_run_without_news_member() -> None:
    with pytest.raises(AppError) as caught:
        await news.get_news_session(SESSION_ID, request_with([completed_row([])]), {"id": "user-9"})

    assert caught.value.code == "news_member_not_found"
