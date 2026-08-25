import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request

from .auth import require_user
from .errors import AppError

router = APIRouter(prefix="/api/news", tags=["news intelligence"])
RequiredUser = Annotated[dict[str, Any], Depends(require_user)]
SessionPath = Annotated[str, Path(pattern=r"^[A-Fa-f0-9-]{36}$")]


def _news_member(row: dict[str, Any]) -> dict[str, Any] | None:
    for response in row.get("member_responses") or []:
        if not isinstance(response, dict) or response.get("agentId") != "news-intelligence-analyst":
            continue
        content = response.get("content")
        if isinstance(content, str) and content.strip():
            return response
    return None


def _preview(markdown: str) -> str:
    without_links = re.sub(r"!?(?:\[([^]]*)\])\([^)]*\)", r"\1", markdown)
    without_markup = re.sub(r"[#*_`>|~\-]+", " ", without_links)
    return " ".join(without_markup.split())[:220]


@router.get("/sessions")
async def list_news_sessions(request: Request, user: RequiredUser) -> dict[str, Any]:
    rows = await request.app.state.db.select(
        "automation_agent_runs",
        {
            "select": "id,agno_run_id,model_id,member_responses,created_at,updated_at",
            "user_id": f"eq.{user['id']}",
            "status": "eq.completed",
            "order": "created_at.desc",
            "limit": "100",
        },
    )
    sessions = []
    for row in rows:
        member = _news_member(row)
        if not member:
            continue
        analysis = str(member["content"])
        sessions.append(
            {
                "sessionId": row["id"],
                "runId": member.get("runId") or row.get("agno_run_id"),
                "model": member.get("model") or row["model_id"],
                "createdAt": member.get("createdAt") or row["created_at"],
                "updatedAt": row["updated_at"],
                "runCount": 1,
                "preview": _preview(analysis),
            }
        )
    return {"success": True, "sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_news_session(session_id: SessionPath, request: Request, user: RequiredUser) -> dict[str, Any]:
    rows = await request.app.state.db.select(
        "automation_agent_runs",
        {
            "select": "id,agno_run_id,model_id,member_responses,created_at",
            "id": f"eq.{session_id}",
            "user_id": f"eq.{user['id']}",
            "status": "eq.completed",
            "limit": "1",
        },
    )
    if not rows:
        raise AppError(404, "News analysis not found", "news_session_not_found")
    row = rows[0]
    member = _news_member(row)
    if not member:
        raise AppError(404, "This main-agent run has no completed news analysis", "news_member_not_found")
    return {
        "success": True,
        "sessionId": row["id"],
        "runId": member.get("runId") or row.get("agno_run_id"),
        "model": member.get("model") or row["model_id"],
        "researchTools": member.get("researchTools") or [],
        "analysis": member["content"],
        "createdAt": member.get("createdAt") or row["created_at"],
        "history": [],
    }
