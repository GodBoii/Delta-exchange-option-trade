import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from news_agent.config import NewsAgentSettings
from news_agent.database import create_session_db, verify_session_db
from news_agent.pipeline import run_news_pipeline

LOG_LEVEL_NAME = os.getenv("NEWS_LOG_LEVEL", "DEBUG").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.DEBUG)


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(threadName)s | %(message)s")
        )
        root.addHandler(handler)
    for handler in root.handlers:
        handler.setLevel(LOG_LEVEL)
    for logger_name in ("news_analyzer", "news_agent", "agno"):
        logging.getLogger(logger_name).setLevel(LOG_LEVEL)
    # Keep application and Agno traces detailed without drowning them in HTTP/2,
    # TLS, DNS, and SDK wire-level chatter. Request/tool lifecycle is logged by us.
    for logger_name in (
        "openai",
        "httpx",
        "httpcore",
        "hpack",
        "h2",
        "rustls",
        "hickory",
        "hickory_net",
        "hickory_proto",
        "hickory_resolver",
        "reqwest",
        "hyper_util",
        "cookie_store",
        "primp",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)
settings = NewsAgentSettings.load()
SessionPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]+$")]
UserQuery = Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]+$")]


class ServiceError(Exception):
    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class NewsAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    sessionId: str = Field(default="btc-news-desk", pattern=r"^[A-Za-z0-9_-]+$")
    userId: str = Field(pattern=r"^[A-Za-z0-9_-]+$")

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


def _markdown_content(content: Any) -> str | None:
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, BaseModel):
        content = content.model_dump(mode="json")
    if isinstance(content, (dict, list)):
        return f"```json\n{json.dumps(content, indent=2, ensure_ascii=False, default=str)}\n```"
    return None


def _valid_outcomes(session: Any) -> list[tuple[str, Any]]:
    if not session or not session.runs:
        return []
    outcomes: list[tuple[str, Any]] = []
    for run in reversed(session.runs):
        markdown = _markdown_content(run.content)
        if markdown:
            outcomes.append((markdown, run))
        else:
            logger.debug("Skipping empty saved news run run_id=%s", getattr(run, "run_id", None))
    return outcomes


def _response_payload(
    *,
    session: Any,
    public_session_id: str,
    elapsed_ms: int | None = None,
    research_tools: list[str] | None = None,
) -> dict[str, Any]:
    outcomes = _valid_outcomes(session)
    if not outcomes:
        raise ServiceError(404, "No saved news analysis was found for this session", "news_session_not_found")
    analysis, run = outcomes[0]
    history = [
        {
            "runId": saved_run.run_id,
            "model": saved_run.model or settings.model_id,
            "analysis": saved_analysis,
            "createdAt": getattr(saved_run, "created_at", None),
        }
        for saved_analysis, saved_run in outcomes[1:]
    ]
    payload: dict[str, Any] = {
        "success": True,
        "sessionId": public_session_id,
        "runId": run.run_id,
        "model": run.model or settings.model_id,
        "researchTools": research_tools if research_tools is not None else _tool_names(session),
        "analysis": analysis,
        "createdAt": getattr(run, "created_at", None),
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


def _session_value(session: Any, name: str, default: Any = None) -> Any:
    return session.get(name, default) if isinstance(session, dict) else getattr(session, name, default)


def _plain_text_preview(markdown: str) -> str:
    without_links = re.sub(r"!?(?:\[([^]]*)\])\([^)]*\)", r"\1", markdown)
    without_markup = re.sub(r"[#*_`>|~\-]+", " ", without_links)
    return " ".join(without_markup.split())[:220]


def _list_sessions(user_id: str) -> dict[str, Any]:
    db = _open_session_db()
    prefix = f"news:{user_id}:"
    try:
        stored_sessions = db.get_sessions(
            user_id=user_id,
            component_id="news-intelligence-analyst",
            limit=None,
            sort_by="updated_at",
            sort_order="desc",
        )
        sessions: list[dict[str, Any]] = []
        for session in stored_sessions:
            stored_id = str(_session_value(session, "session_id", ""))
            if not stored_id.startswith(prefix):
                continue
            outcomes = _valid_outcomes(session)
            if not outcomes:
                continue
            latest_analysis, latest_run = outcomes[0]
            sessions.append(
                {
                    "sessionId": stored_id[len(prefix) :],
                    "runId": getattr(latest_run, "run_id", None),
                    "model": getattr(latest_run, "model", None) or settings.model_id,
                    "createdAt": _session_value(session, "created_at"),
                    "updatedAt": _session_value(session, "updated_at"),
                    "runCount": len(outcomes),
                    "preview": _plain_text_preview(latest_analysis),
                }
            )
        logger.info("Listed news sessions user_id=%s count=%d", user_id, len(sessions))
        return {"success": True, "sessions": sessions}
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
    started_at = time.perf_counter()
    try:
        logger.debug("Opening news session database schema=%s table=%s", settings.db_schema, settings.session_table)
        db = create_session_db(settings)
        verify_session_db(db)
        logger.debug("News session database ready elapsed_ms=%d", round((time.perf_counter() - started_at) * 1_000))
        return db
    except Exception as exc:
        logger.exception(
            "News session database open failed elapsed_ms=%d",
            round((time.perf_counter() - started_at) * 1_000),
        )
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


def _run_analysis(body: NewsAnalysisRequest, trace_id: str = "untracked") -> dict[str, Any]:
    stored_session_id = _stored_session_id(body.userId, body.sessionId)
    started_at = time.perf_counter()
    logger.info(
        "Analysis started trace_id=%s model=%s session_id=%s stored_session_id=%s user_id=%s query_chars=%d",
        trace_id,
        settings.model_id,
        body.sessionId,
        stored_session_id,
        body.userId,
        len(body.query),
    )
    logger.debug("Analysis query trace_id=%s query=%r", trace_id, body.query)
    db = _open_session_db()
    try:
        result = run_news_pipeline(
            body.query,
            settings=settings,
            session_id=stored_session_id,
            user_id=body.userId,
            db=db,
            debug_mode=True,
        )
        if result.markdown is None:
            logger.error(
                "Analysis produced empty output trace_id=%s run_id=%s content_type=%s content=%r",
                trace_id,
                result.report_response.run_id,
                type(result.report_response.content).__name__,
                result.report_response.content,
            )
            raise ServiceError(502, "The news agent returned an empty analysis", "empty_news_analysis")
        session = db.get_session(stored_session_id, user_id=body.userId)
        elapsed_ms = round((time.perf_counter() - started_at) * 1_000)
        payload = _response_payload(
            session=session,
            public_session_id=body.sessionId,
            elapsed_ms=elapsed_ms,
            research_tools=getattr(result, "research_tools", []),
        )
        logger.info(
            "Analysis completed trace_id=%s run_id=%s elapsed_ms=%d markdown_chars=%d tools=%s",
            trace_id,
            payload["runId"],
            elapsed_ms,
            len(result.markdown),
            getattr(result, "research_tools", []),
        )
        return payload
    except ServiceError:
        logger.exception(
            "Analysis service error trace_id=%s elapsed_ms=%d",
            trace_id,
            round((time.perf_counter() - started_at) * 1_000),
        )
        raise
    except Exception as exc:
        logger.exception(
            "News analysis run failed trace_id=%s elapsed_ms=%d",
            trace_id,
            round((time.perf_counter() - started_at) * 1_000),
            exc_info=exc,
        )
        raise ServiceError(
            502,
            "The news analysis could not be completed. Please try again.",
            "news_agent_failed",
        ) from exc
    finally:
        db.close()
        logger.debug("Analysis database closed trace_id=%s", trace_id)


app = FastAPI(title="News Analyzer", version="1.0.0", docs_url="/docs", redoc_url=None)


@app.middleware("http")
async def log_request(request: Request, call_next):
    trace_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.trace_id = trace_id
    started_at = time.perf_counter()
    request_level = logging.DEBUG if request.url.path == "/health" else logging.INFO
    logger.log(
        request_level,
        "HTTP request started trace_id=%s method=%s path=%s client=%s",
        trace_id,
        request.method,
        request.url.path,
        request.client.host if request.client else "unknown",
    )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "HTTP request crashed trace_id=%s method=%s path=%s elapsed_ms=%d",
            trace_id,
            request.method,
            request.url.path,
            round((time.perf_counter() - started_at) * 1_000),
        )
        raise
    response.headers["x-request-id"] = trace_id
    logger.log(
        request_level,
        "HTTP request completed trace_id=%s method=%s path=%s status=%d elapsed_ms=%d",
        trace_id,
        request.method,
        request.url.path,
        response.status_code,
        round((time.perf_counter() - started_at) * 1_000),
    )
    return response


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, error: ServiceError) -> JSONResponse:
    logger.error(
        "Service error trace_id=%s status=%d code=%s message=%s",
        getattr(request.state, "trace_id", "untracked"),
        error.status,
        error.code,
        error.message,
    )
    return JSONResponse(
        status_code=error.status,
        content={"success": False, "error": {"code": error.code, "message": error.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
    logger.warning(
        "Validation error trace_id=%s errors=%s",
        getattr(request.state, "trace_id", "untracked"),
        error.errors(),
    )
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": {"code": "validation_error", "message": str(error)}},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled News Analyzer error trace_id=%s",
        getattr(request.state, "trace_id", "untracked"),
        exc_info=error,
    )
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


@app.get("/v1/sessions")
async def list_news_sessions(userId: UserQuery) -> dict[str, Any]:
    return await asyncio.to_thread(_list_sessions, userId)


@app.post("/v1/analyze")
async def analyze_news(body: NewsAnalysisRequest, request: Request) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        raise ServiceError(503, "News analysis is temporarily unavailable", "news_agent_not_configured")
    if not settings.supabase_db_url:
        raise ServiceError(
            503,
            "News analysis is temporarily unavailable",
            "news_database_not_configured",
        )
    return await asyncio.to_thread(_run_analysis, body, request.state.trace_id)
