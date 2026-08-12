import asyncio
import logging
import time
from typing import Annotated, Any

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from news_agent.config import NewsAgentSettings
from news_agent.database import create_session_db, verify_session_db
from news_agent.models import NewsAnalysisReport
from news_agent.pipeline import run_news_pipeline

logger = logging.getLogger(__name__)
settings = NewsAgentSettings.load()
news_run_lock = asyncio.Lock()

SessionPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
UserQuery = Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class ServiceError(Exception):
    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class NewsAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    sessionId: str = Field(default="btc-news-desk", pattern=r"^[A-Za-z0-9_-]{1,80}$")
    userId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())


def _stored_session_id(user_id: str, public_session_id: str) -> str:
    return f"news:{user_id}:{public_session_id}"


def _tool_names(session: Any) -> list[str]:
    if not session or not session.runs:
        return []
    names: list[str] = []
    for execution in session.runs[-1].tools or []:
        if isinstance(execution, dict):
            name = execution.get("tool_name") or execution.get("name")
        else:
            name = getattr(execution, "tool_name", None) or getattr(execution, "name", None)
        if name:
            names.append(str(name))
    return names


def _parse_saved_report(content: Any) -> NewsAnalysisReport:
    if isinstance(content, str):
        return NewsAnalysisReport.model_validate_json(content)
    return NewsAnalysisReport.model_validate(content)


def _valid_outcomes(session: Any) -> list[tuple[NewsAnalysisReport, Any]]:
    if not session or not session.runs:
        return []
    outcomes: list[tuple[NewsAnalysisReport, Any]] = []
    for run in reversed(session.runs):
        try:
            outcomes.append((_parse_saved_report(run.content), run))
        except Exception:
            continue
    return outcomes


def _response_payload(
    *,
    session: Any,
    public_session_id: str,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    outcomes = _valid_outcomes(session)
    if not outcomes:
        raise ServiceError(404, "No saved news analysis was found for this session", "news_session_not_found")
    report, run = outcomes[0]
    history = [
        {
            "runId": saved_run.run_id,
            "model": saved_run.model or settings.model_id,
            "report": saved_report.model_dump(mode="json"),
        }
        for saved_report, saved_run in outcomes[1:]
    ]
    payload: dict[str, Any] = {
        "success": True,
        "sessionId": public_session_id,
        "runId": run.run_id,
        "model": run.model or settings.model_id,
        "researchTools": _tool_names(session),
        "report": report.model_dump(mode="json"),
        "history": history,
    }
    if elapsed_ms is not None:
        payload["elapsedMs"] = elapsed_ms
    return payload


def _load_response(user_id: str, session_id: str, elapsed_ms: int | None = None) -> dict[str, Any]:
    stored_session_id = _stored_session_id(user_id, session_id)
    db = _open_session_db()
    try:
        session = db.get_session(stored_session_id, user_id=user_id)
        return _response_payload(
            session=session,
            public_session_id=session_id,
            elapsed_ms=elapsed_ms,
        )
    finally:
        db.close()


def _database_service_error(error: Exception) -> ServiceError:
    if isinstance(error, RuntimeError):
        message = str(error)
        code = "news_database_not_configured"
    else:
        message = (
            "News session storage cannot connect to Supabase. Verify SUPABASE_DB_URL uses the Session pooler URI "
            "for this project and the current database password."
        )
        code = "news_database_unavailable"
    return ServiceError(503, message, code)


def _open_session_db():
    db = None
    try:
        db = create_session_db(settings)
        verify_session_db(db)
        return db
    except Exception as exc:
        if db is not None:
            db.close()
        raise _database_service_error(exc) from exc


def _database_status() -> tuple[bool, str | None]:
    db = None
    try:
        db = create_session_db(settings)
        verify_session_db(db)
        return True, None
    except Exception as exc:
        return False, _database_service_error(exc).message
    finally:
        if db is not None:
            db.close()


def _run_analysis(body: NewsAnalysisRequest) -> dict[str, Any]:
    stored_session_id = _stored_session_id(body.userId, body.sessionId)
    started_at = time.perf_counter()
    db = _open_session_db()
    try:
        result = run_news_pipeline(
            body.query,
            settings=settings,
            session_id=stored_session_id,
            user_id=body.userId,
            db=db,
        )
        if result.report is None:
            raise ServiceError(502, "The news agent returned an invalid report", "invalid_news_report")
        session = db.get_session(stored_session_id, user_id=body.userId)
        elapsed_ms = round((time.perf_counter() - started_at) * 1_000)
        return _response_payload(
            session=session,
            public_session_id=body.sessionId,
            elapsed_ms=elapsed_ms,
        )
    except ServiceError:
        raise
    except Exception as exc:
        logger.exception("News analysis run failed", exc_info=exc)
        raise ServiceError(
            502,
            "The news agent could not complete the analysis. Check the News Analyzer logs for details.",
            "news_agent_failed",
        ) from exc
    finally:
        db.close()


app = FastAPI(title="News Analyzer", version="1.0.0", docs_url="/docs", redoc_url=None)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, error: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"success": False, "error": {"code": error.code, "message": error.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": {"code": "validation_error", "message": str(error)}},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, error: Exception) -> JSONResponse:
    logger.exception("Unhandled News Analyzer error", exc_info=error)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": {"code": "internal_error", "message": "Unexpected server error"}},
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    database_ready, database_error = await asyncio.to_thread(_database_status)
    return {
        "success": True,
        "service": "news-analyzer",
        "database": "supabase-postgres",
        "databaseConfigured": bool(settings.supabase_db_url),
        "databaseReady": database_ready,
        "databaseError": database_error,
        "databaseSchema": settings.db_schema,
        "sessionTable": settings.session_table,
        "model": settings.model_id,
    }


@app.get("/v1/sessions/{session_id}")
async def get_news_session(session_id: SessionPath, userId: UserQuery) -> dict[str, Any]:
    return await asyncio.to_thread(_load_response, userId, session_id)


@app.post("/v1/analyze")
async def analyze_news(body: NewsAnalysisRequest) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        raise ServiceError(503, "The news agent API key is not configured", "news_agent_not_configured")
    if not settings.supabase_db_url:
        raise ServiceError(
            503,
            "SUPABASE_DB_URL is not configured for News Analyzer",
            "news_database_not_configured",
        )
    async with news_run_lock:
        return await asyncio.to_thread(_run_analysis, body)
