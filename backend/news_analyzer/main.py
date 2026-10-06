import asyncio
import hmac
import json
import logging
import os
import sys
import time
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.assets import DEFAULT_ASSET, Asset
from app.decision_report import replace_model_decision
from app.errors import MARKET_AUTH_ERROR_CODE, MARKET_AUTH_ERROR_MESSAGE
from app.shared_analysis import SHARED_USER_ID
from automation_agent.assets import asset_profile
from automation_agent.team import run_activation_recheck, run_automation_team
from automation_agent.tools import confirm_activation_recheck, read_automation_state
from news_agent.config import RECHECK_TIMEOUT_SECONDS, NewsAgentSettings
from news_agent.database import create_session_db, verify_session_db
from news_analyzer.worker import run_in_worker

LOG_LEVEL_NAME = os.getenv("NEWS_LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(threadName)s | %(message)s"))
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


class ServiceError(Exception):
    def __init__(self, status: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class AutomationAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userId: str = Field(pattern=r"^(global|[A-Fa-f0-9-]{36})$")
    agentRunId: str = Field(pattern=r"^[A-Fa-f0-9-]{36}$")
    sessionId: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    accountContext: dict[str, Any]
    trigger: str = Field(
        pattern=(
            r"^(manual|asia_session|london_session|new_york_session|"
            r"pre_expiry|midnight_review|agent_follow_up|activation_recheck)$"
        )
    )
    triggerReason: str | None = Field(default=None, max_length=1000)
    signalsToInspect: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=10)
    # Omitted by callers that predate ETH; those runs belong to the BTC agent.
    asset: Asset = DEFAULT_ASSET


def _database_service_error(error: Exception) -> ServiceError:
    if isinstance(error, RuntimeError):
        message = str(error)
        code = "news_database_not_configured"
    else:
        message = (
            "News session storage cannot connect to the local PostgreSQL server. Verify AI_DATABASE_URL and the "
            "analysis role password."
        )
        code = "news_database_unavailable"
    return ServiceError(503, message, code)


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


def _run_automation_analysis(body: AutomationAnalysisRequest, trace_id: str) -> dict[str, Any]:
    started_at = time.perf_counter()
    logger.info(
        "Automation analysis started trace_id=%s run_id=%s user_id=%s asset=%s model=%s",
        trace_id,
        body.agentRunId,
        body.userId,
        body.asset,
        settings.automation_model_id,
    )
    try:
        if body.trigger == "activation_recheck":
            result = run_activation_recheck(
                settings=settings,
                user_id=body.userId,
                agent_run_id=body.agentRunId,
                session_id=body.sessionId,
                recheck_context=body.accountContext,
                asset=asset_profile(body.asset),
            )
            if result.tool_calls:
                recorded = read_automation_state(settings, user_id=body.userId, agent_run_id=body.agentRunId)
                if not recorded or recorded.get("outcome") != "strategy_dropped":
                    raise RuntimeError("Activation recheck attempted a drop but cancellation was not recorded")
            else:
                confirm_activation_recheck(
                    settings,
                    user_id=body.userId,
                    agent_run_id=body.agentRunId,
                    proposal_id=str(body.accountContext["proposalId"]),
                )
        else:
            result = run_automation_team(
                settings=settings,
                user_id=body.userId,
                agent_run_id=body.agentRunId,
                session_id=body.sessionId,
                account_context=body.accountContext,
                trigger=body.trigger,
                trigger_reason=body.triggerReason,
                signals_to_inspect=body.signalsToInspect,
                asset=asset_profile(body.asset),
            )
        state = read_automation_state(
            settings,
            user_id=body.userId,
            agent_run_id=body.agentRunId,
        )
        if not state or not state.get("outcome"):
            for call in result.tool_calls:
                if call.get("name") != "preview_strategy":
                    continue
                try:
                    preview = json.loads(call.get("result") or "{}")
                except (ValueError, TypeError):
                    continue
                if isinstance(preview, dict) and preview.get("errorCode") == MARKET_AUTH_ERROR_CODE:
                    raise ServiceError(503, MARKET_AUTH_ERROR_MESSAGE, MARKET_AUTH_ERROR_CODE)
        outcome = str(state.get("outcome") or "no_trade_for_current_window") if state else "no_trade_for_current_window"
        report = (
            result.report if body.trigger == "activation_recheck"
            else replace_model_decision(outcome, result.report, shared=body.userId == SHARED_USER_ID)
        )
        return {
            "success": True,
            "runId": result.run_id,
            "sessionId": result.session_id,
            "agentRunId": body.agentRunId,
            "model": result.model_id,
            "outcome": outcome,
            "report": report,
            "marketSnapshotId": result.market_snapshot_id,
            "memberResponses": result.member_responses,
            "toolCalls": result.tool_calls,
            "elapsedMs": round((time.perf_counter() - started_at) * 1_000),
        }
    except Exception as exc:
        try:
            state = read_automation_state(
                settings,
                user_id=body.userId,
                agent_run_id=body.agentRunId,
            )
        except Exception:
            logger.exception("Could not recover automation state run_id=%s", body.agentRunId)
            state = None
        if state and state.get("outcome"):
            logger.exception(
                "Automation report failed after terminal action trace_id=%s run_id=%s outcome=%s",
                trace_id,
                body.agentRunId,
                state["outcome"],
            )
            return {
                "success": True,
                "runId": None,
                "sessionId": f"automation:{body.userId}:{body.sessionId}",
                "agentRunId": body.agentRunId,
                "model": settings.automation_model_id,
                "outcome": state["outcome"],
                "report": (
                    "## Decision\n\nThe terminal action was recorded, "
                    "but the model provider did not return the final report."
                ),
                "marketSnapshotId": state.get("market_snapshot_id"),
                "memberResponses": [],
                "toolCalls": [],
                "elapsedMs": round((time.perf_counter() - started_at) * 1_000),
            }
        logger.exception(
            "Automation analysis failed trace_id=%s run_id=%s elapsed_ms=%d",
            trace_id,
            body.agentRunId,
            round((time.perf_counter() - started_at) * 1_000),
            exc_info=exc,
        )
        if isinstance(exc, ServiceError):
            raise
        raise ServiceError(
            502,
            "The automation analysis could not be completed. No strategy was activated.",
            "automation_agent_failed",
        ) from exc


app = FastAPI(title="News Analyzer", version="1.0.0", docs_url="/docs", redoc_url=None)


@app.middleware("http")
async def authenticate_internal_request(request: Request, call_next):
    if request.url.path.startswith("/v1/") and settings.analysis_service_secret:
        supplied = request.headers.get("X-Analysis-Secret", "")
        if not hmac.compare_digest(supplied, settings.analysis_service_secret):
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": {"code": "service_unauthorized", "message": "Service authentication required"},
                },
            )
    return await call_next(request)


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
        "database": "local-postgres",
        "databaseConfigured": bool(settings.database_url),
        "databaseReady": database_ready,
        "databaseError": database_error,
        "databaseSchema": settings.db_schema,
        "sessionTable": settings.session_table,
        "model": settings.model_id,
    }


@app.post("/v1/automation/analyze")
async def analyze_automation(body: AutomationAnalysisRequest, request: Request) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        raise ServiceError(503, "Automation analysis is temporarily unavailable", "automation_agent_not_configured")
    if not settings.database_url:
        raise ServiceError(503, "Automation storage is temporarily unavailable", "automation_database_not_configured")
    try:
        return await asyncio.to_thread(
            run_in_worker, _run_automation_analysis, body, request.state.trace_id,
            timeout_seconds=RECHECK_TIMEOUT_SECONDS if body.trigger == "activation_recheck" else 45 * 60,
        )
    except TimeoutError as exc:
        raise ServiceError(504, str(exc), "automation_analysis_timeout") from exc
    except RuntimeError as exc:
        if str(exc) == MARKET_AUTH_ERROR_MESSAGE:
            raise ServiceError(503, MARKET_AUTH_ERROR_MESSAGE, MARKET_AUTH_ERROR_CODE) from exc
        raise ServiceError(502, "Automation analysis could not be completed", "automation_agent_failed") from exc
