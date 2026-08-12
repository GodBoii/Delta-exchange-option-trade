from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import require_user
from .errors import AppError

router = APIRouter(prefix="/api/news", tags=["news intelligence"])
news_analyzer_url = "http://news-analyzer:8002"

RequiredUser = Annotated[dict[str, Any], Depends(require_user)]
SessionPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,80}$")]


class NewsAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    sessionId: str = Field(default="btc-news-desk", pattern=r"^[A-Za-z0-9_-]{1,80}$")

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())


async def _news_analyzer_request(
    method: str,
    path: str,
    *,
    user_id: str,
    body: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            response = await client.request(
                method,
                f"{news_analyzer_url.rstrip('/')}{path}",
                params={"userId": user_id} if method == "GET" else None,
                json={**(body or {}), "userId": user_id} if method != "GET" else None,
            )
    except httpx.HTTPError as exc:
        raise AppError(503, "News Analyzer is unavailable", "news_analyzer_unavailable") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(502, "News Analyzer returned an invalid response", "invalid_news_analyzer_response") from exc
    if response.is_error:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        raise AppError(response.status_code, message or "News Analyzer request failed", code or "news_analyzer_failed")
    if not isinstance(payload, dict):
        raise AppError(502, "News Analyzer returned an invalid response", "invalid_news_analyzer_response")
    return payload


@router.get("/sessions/{session_id}")
async def get_news_session(session_id: SessionPath, user: RequiredUser) -> dict[str, Any]:
    return await _news_analyzer_request(
        "GET",
        f"/v1/sessions/{session_id}",
        user_id=str(user["id"]),
    )


@router.post("/analyze")
async def analyze_news(body: NewsAnalysisRequest, user: RequiredUser) -> dict[str, Any]:
    return await _news_analyzer_request(
        "POST",
        "/v1/analyze",
        user_id=str(user["id"]),
        body=body.model_dump(),
        timeout=300.0,
    )
