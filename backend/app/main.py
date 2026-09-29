import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

from scripts.database_roles import ensure_roles
from scripts.init_local_db import apply_migrations

from .auth import (
    create_connection,
    current_account,
    delta_client_for_user,
    optional_user,
    remove_connection,
    require_user,
)
from .automation import AutomationScheduler
from .automation import router as automation_router
from .capital import capital_budget, maximum_concurrent_strategies
from .change_feed import ChangeFeed
from .config import get_settings
from .database import Database
from .delta import DeltaClient
from .engine import Scheduler, TradingEngine
from .errors import AppError
from .exit_schedule import ExitChoice, fetch_option_catalog, resolve_exit_schedule
from .instance_lock import InstanceLock
from .models import (
    CancelOrderRequest,
    CapitalSettingsUpdate,
    ClosePositionRequest,
    ConnectRequest,
    SaveStrategyRequest,
    StrategyDefinition,
)
from .portfolio_stream import serve_portfolio
from .push import PushNotifier
from .push_api import router as push_router
from .reporting_api import WalletProbe
from .reporting_api import router as reporting_router
from .strategy import delta_expiry

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.trading_writer_enabled:
        await asyncio.to_thread(apply_migrations, settings.local_database_url)
        await asyncio.to_thread(
            ensure_roles,
            settings.local_database_url,
            ai_url=settings.ai_database_url,
            reader_url=settings.local_reader_database_url,
        )
    pool = AsyncConnectionPool(settings.database_url, min_size=2, max_size=settings.database_pool_size, open=False)
    await pool.open(wait=True)
    instance_lock = InstanceLock(settings.trading_lock_path) if settings.trading_writer_enabled else None
    if instance_lock is not None:
        instance_lock.acquire()
    db = Database(settings, pool)
    change_feed = ChangeFeed(settings.database_url)
    change_feed.start()
    engine_settings = (
        settings if settings.trading_writer_enabled else settings.model_copy(update={"delta_events_enabled": False})
    )
    # Only the writer changes trade state, so only the writer sends trade alerts.
    push = PushNotifier(
        pool,
        public_key=settings.vapid_public_key,
        private_key=settings.vapid_private_key if settings.trading_writer_enabled else None,
        subject=settings.vapid_subject,
    )
    if settings.trading_writer_enabled and not push.enabled:
        logger.warning("Phone notifications are off: VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are not set")
    engine = TradingEngine(db, engine_settings, notifier=push)
    scheduler = Scheduler(
        engine,
        settings.scheduler_poll_seconds,
        settings.scheduler_enabled and settings.trading_writer_enabled,
    )
    automation_scheduler = AutomationScheduler(db, engine)
    app.state.db = db
    app.state.change_feed = change_feed
    app.state.engine = engine
    app.state.push = push
    app.state.scheduler = scheduler
    app.state.automation_scheduler = automation_scheduler
    if settings.trading_writer_enabled:
        try:
            response = await db.client.get("https://api.ipify.org", timeout=5)
            response.raise_for_status()
            outbound_ip = str(ip_address(response.text.strip()))
            await db.runtime.data.request("accounts:updateOutboundIp", {"ip": outbound_ip}, mutation=True)
        except Exception:
            logger.exception("Could not refresh the server outbound IP")
    app.state.wallet_probe = WalletProbe()
    if settings.trading_writer_enabled:
        # Reporting only: an import failure leaves history marked incomplete and never blocks trading.
        # Deletion archives inside its own transaction, so it never depends on this import.
        try:
            await db.runtime.ledger.ensure_backfilled()
        except Exception:
            logger.exception("Owner ledger backfill failed; historical P&L stays marked incomplete")
    if settings.trading_writer_enabled and not settings.scheduler_enabled:
        await engine.recover_interrupted_states()
    scheduler.start()
    if settings.trading_writer_enabled and settings.automation_scheduler_enabled:
        automation_scheduler.start()
    try:
        yield
    finally:
        await automation_scheduler.stop()
        await scheduler.stop()
        await change_feed.stop()
        await engine.close()
        await db.close()
        await pool.close()
        if instance_lock is not None:
            instance_lock.close()


app = FastAPI(
    title="Delta Strategy Desk API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def require_trading_writer(request: Request, call_next):
    if not settings.trading_writer_enabled and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": {
                    "code": "read_replica",
                    "message": "This API replica only serves reads; route changes to the trading writer",
                },
            },
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=settings.frontend_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(automation_router)
app.include_router(reporting_router)
app.include_router(push_router)

RequiredUser = Annotated[dict[str, Any], Depends(require_user)]
OptionalUser = Annotated[dict[str, Any] | None, Depends(optional_user)]


class ResearchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Literal[
        "accounts:overview",
        "library:getCapital",
        "library:serverGet",
        "library:serverList",
        "runtimeAutomation:context",
        "runtimeAutomation:saveSnapshot",
        "runtimeAutomation:schedule",
        "runtimeAutomation:recheck",
        "runtimeAutomation:followup",
        "sharedAnalysis:publish",
    ]
    args: dict[str, Any]
    mutation: bool = False


class LibrarySave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    definitionJson: str
    enabled: bool
    expectedVersion: int | None


@app.get("/api/library")
async def library(request: Request, user: RequiredUser) -> dict[str, Any]:
    rows = await request.app.state.db.local_data.saved_strategies(str(user["id"]))
    return {"success": True, "result": rows}


@app.put("/api/library/{strategy_id}")
async def library_save(request: Request, strategy_id: str, body: LibrarySave, user: RequiredUser) -> dict[str, Any]:
    if strategy_id != body.id:
        raise AppError(422, "Strategy identifier mismatch", "library_identifier_mismatch")
    row = await request.app.state.db.local_data.library_save(str(user["id"]), body.model_dump())
    return {"success": True, "result": request.app.state.db.local_data._legacy_library_row(row)}


@app.delete("/api/library/{strategy_id}")
async def library_remove(
    request: Request, strategy_id: str, expectedVersion: int, user: RequiredUser
) -> dict[str, bool]:
    await request.app.state.db.local_data.library_remove(str(user["id"]), strategy_id, expectedVersion)
    return {"success": True}


REVISION_HEARTBEAT_SECONDS = 15.0


@app.get("/api/revisions")
async def revisions(request: Request, user: RequiredUser) -> StreamingResponse:
    feed: ChangeFeed = request.app.state.change_feed
    subscription = feed.subscribe(str(user["id"]))

    async def events():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    await asyncio.wait_for(subscription.changed.wait(), timeout=REVISION_HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": alive\n\n"
                    continue
                yield f"data: {json.dumps(subscription.take())}\n\n"
        finally:
            feed.unsubscribe(subscription)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/charts/{run_id}/{chart_id}")
async def chart_image(request: Request, run_id: str, chart_id: str, expires: int, signature: str) -> Response:
    db: Database = request.app.state.db
    db.charts.verify(run_id, chart_id, expires, signature)
    content = await db.charts.content(run_id, chart_id)
    return Response(
        content,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600, immutable", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/internal/research")
async def research_operation(
    request: Request, body: ResearchOperation, x_analysis_secret: str | None = Header(default=None)
) -> dict[str, Any]:
    if not hmac.compare_digest(x_analysis_secret or "", settings.analysis_service_secret):
        raise AppError(401, "Service authentication required", "service_unauthorized")
    result = await request.app.state.db.local_data.request(body.path, body.args, mutation=body.mutation)
    return {"status": "success", "value": result}


@app.exception_handler(AppError)
async def app_error_handler(_: Request, error: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"success": False, "error": {"code": error.code, "message": error.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "error": {
                "code": "validation_error",
                "message": "Please correct the highlighted fields",
                "issues": jsonable_encoder(error.errors()),
            },
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, error: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", exc_info=error)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": {"code": "internal_error", "message": "Unexpected server error"}},
    )


PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "forwarded")


def is_local_probe(request: Request) -> bool:
    """True only for a direct loopback call: container healthchecks and scripts/start-backend.ps1.

    Tunnelled traffic can also arrive from loopback when cloudflared runs on the same host,
    so any proxy header marks the request as public regardless of the socket address.
    """
    if any(request.headers.get(name) for name in PROXY_HEADERS):
        return False
    host = request.client.host if request.client else ""
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    # The browser only needs to identify the service. Scheduler, database and stream
    # state stay private to local probes.
    if not is_local_probe(request):
        return {"success": True, "service": "delta-strategy-api"}
    scheduler: Scheduler = request.app.state.scheduler
    feed: ChangeFeed = request.app.state.change_feed
    pool: AsyncConnectionPool = request.app.state.db.pool
    stats = pool.get_stats()
    return {
        "success": True,
        "service": "delta-strategy-api",
        "role": "writer" if settings.trading_writer_enabled else "reader",
        "scheduler": scheduler.status(),
        "database": {
            "poolSize": stats.get("pool_size", 0),
            "poolAvailable": stats.get("pool_available", 0),
            "requestsWaiting": stats.get("requests_waiting", 0),
        },
        "changeFeed": {"connected": feed.connected, "subscribers": sum(map(len, feed.subscribers.values()))},
    }


@app.get("/api/session")
async def session(request: Request, user: OptionalUser) -> dict[str, Any]:
    db: Database = request.app.state.db
    account = await current_account(db, user, required=False)
    return {
        "success": True,
        "authenticated": account is not None,
        "connected": bool(account and account["connection_id"]),
        "user": {
            "id": account["id"],
            "email": account["app_email"],
            "displayName": account["display_name"],
            "avatarUrl": account["avatar_url"],
            "userType": account["user_type"],
            "phoneNumber": account["phone_number"],
        }
        if account
        else None,
        "account": {
            "id": account["delta_user_id"],
            "accountName": account["account_name"],
            "email": account["email_masked"],
            "environment": "production",
        }
        if account and account["connection_id"]
        else None,
    }


@app.post("/api/session/connect")
async def connect_delta(request: Request, body: ConnectRequest, user: RequiredUser) -> dict[str, Any]:
    result = await create_connection(request.app.state.db, settings, str(user["id"]), body.apiKey, body.apiSecret)
    await request.app.state.engine.invalidate_credentials(str(user["id"]))
    return {"success": True, "account": result["account"]}


@app.delete("/api/session")
async def disconnect_delta(request: Request, user: RequiredUser) -> dict[str, bool]:
    await remove_connection(request.app.state.db, str(user["id"]))
    await request.app.state.engine.invalidate_credentials(str(user["id"]))
    return {"success": True}


@app.get("/api/account/overview")
async def account_overview(request: Request, user: RequiredUser) -> dict[str, Any]:
    db: Database = request.app.state.db
    account = await current_account(db, user, required=True)
    client = await delta_client_for_user(db, settings, str(user["id"]))
    try:
        balances, orders, positions, risk_strategies = await asyncio.gather(
            client.balances(),
            client.open_orders(),
            client.positions(),
            db.select(
                "strategies",
                {
                    "select": "id,name,status,risk_state,risk_monitor_at,combined_stop_triggered_at",
                    "user_id": f"eq.{user['id']}",
                    "status": "in.(active,executing_exit,attention)",
                    "order": "updated_at.desc",
                    "limit": "12",
                },
            ),
        )
    finally:
        await client.close()
    return {
        "success": True,
        "account": {
            "id": account["delta_user_id"],
            "name": account["account_name"],
            "environment": "production",
        },
        "balances": balances["result"],
        "balanceMeta": balances.get("meta", {}),
        "orders": orders["result"],
        "positions": positions["result"],
        "riskStrategies": [
            {
                "id": item["id"],
                "name": item["name"],
                "status": item["status"],
                "riskState": item.get("risk_state") or {},
                "monitoredAt": item.get("risk_monitor_at"),
                "triggeredAt": item.get("combined_stop_triggered_at"),
            }
            for item in risk_strategies
            if (item.get("risk_state") or {}).get("mode") == "combined_premium"
        ],
    }


@app.websocket("/ws/portfolio")
async def portfolio_stream(websocket: WebSocket) -> None:
    """Live positions, orders, wallet rows and mark/index prices; see ``portfolio_stream``."""
    await serve_portfolio(websocket, websocket.app.state.db, websocket.app.state.engine, settings)


async def capital_overview(request: Request, user_id: str) -> dict[str, Any]:
    engine: TradingEngine = request.app.state.engine
    policy = await engine.capital_policy(user_id)
    client = await engine.client_for_user(user_id)
    try:
        available, total_balance = await engine.usd_capital(client)
    finally:
        await client.close()
    if settings.trading_writer_enabled:
        try:
            # Throttled to one live observation per user every 15 minutes.
            await request.app.state.db.runtime.ledger.record_wallet(
                user_id, total_balance, available, datetime.now(UTC)
            )
        except Exception:
            logger.exception("Could not record a wallet observation user=%s", user_id)
    maximum_slots = maximum_concurrent_strategies(
        total_balance,
        policy.allocation_mode,
        policy.capital_amount,
    )
    slots = await request.app.state.db.select(
        "strategy_capital_slots",
        {
            "select": "id",
            "user_id": f"eq.{user_id}",
            "status": "in.(reserved,active)",
            "limit": "100",
        },
    )
    nominal_budget = capital_budget(
        total_balance,
        total_balance,
        policy.allocation_mode,
        policy.capital_amount,
    )
    next_budget = capital_budget(
        available,
        total_balance,
        policy.allocation_mode,
        policy.capital_amount,
    )
    occupied = len(slots)
    return {
        "success": True,
        "settings": {
            "allocationMode": policy.allocation_mode,
            "capitalAmount": float(policy.capital_amount) if policy.capital_amount is not None else None,
        },
        "wallet": {
            "asset": "USD",
            "totalBalance": float(total_balance),
            "availableBalance": float(available),
        },
        "nominalBudgetPerStrategy": float(nominal_budget),
        "availableBudgetForNextStrategy": float(next_budget),
        "maximumConcurrentStrategies": maximum_slots,
        "occupiedAllocations": occupied,
        "availableAllocations": max(0, maximum_slots - occupied),
    }


@app.get("/api/capital/settings")
async def get_capital_settings(request: Request, user: RequiredUser) -> dict[str, Any]:
    await current_account(request.app.state.db, user, required=True)
    return await capital_overview(request, str(user["id"]))


@app.put("/api/capital/settings")
async def update_capital_settings(
    request: Request,
    body: CapitalSettingsUpdate,
    user: RequiredUser,
) -> dict[str, Any]:
    await current_account(request.app.state.db, user, required=True)
    await request.app.state.engine.save_capital_policy(str(user["id"]), body.allocationMode, body.capitalAmount)
    return await capital_overview(request, str(user["id"]))


@app.get("/api/market/options")
async def market_options(
    _user: RequiredUser,
    underlying: Annotated[str, Query(pattern="^(BTC|ETH)$")],
    expiry: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
) -> dict[str, Any]:
    client = DeltaClient(settings)
    try:
        return await client.option_chain(underlying, delta_expiry(expiry))
    finally:
        await client.close()


@app.get("/api/market/products")
async def market_products(
    _user: RequiredUser,
    contractTypes: str = "perpetual_futures,futures,call_options,put_options",
    expiry: str | None = None,
    pageSize: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    client = DeltaClient(settings)
    try:
        return await client.products(
            {
                "contract_types": contractTypes,
                "states": "live,upcoming",
                "expiry": expiry,
                "page_size": pageSize,
            }
        )
    finally:
        await client.close()


@app.delete("/api/orders/{order_id}")
async def cancel_order(request: Request, order_id: int, body: CancelOrderRequest, user: RequiredUser) -> dict[str, Any]:
    client = await delta_client_for_user(request.app.state.db, settings, str(user["id"]))
    try:
        return await client.cancel_order(order_id, body.productId)
    finally:
        await client.close()


@app.post("/api/positions/{product_id}/close")
async def close_position(
    request: Request, product_id: int, body: ClosePositionRequest, user: RequiredUser
) -> dict[str, Any]:
    result = await request.app.state.engine.close_account_position(str(user["id"]), product_id)
    return {"success": True, "result": result}


@app.get("/api/strategies")
async def list_strategies(request: Request, user: RequiredUser) -> dict[str, Any]:
    account = await current_account(request.app.state.db, user, required=True)
    rows = await request.app.state.db.select(
        "strategies",
        {
            "select": (
                "id,name,status,entry_at,exit_at,entry_execution_at,exit_execution_at,last_error,risk_state,created_at"
            ),
            "user_id": f"eq.{account['id']}",
            "order": "created_at.desc",
            "limit": "100",
        },
    )
    return {
        "success": True,
        "result": [
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "entryAt": row["entry_at"],
                "exitAt": row["exit_at"],
                "entryExecutedAt": row["entry_execution_at"],
                "exitExecutedAt": row["exit_execution_at"],
                "lastError": row["last_error"],
                "exposureStatus": (row.get("risk_state") or {}).get("exposureStatus"),
                "createdAt": row["created_at"],
            }
            for row in rows
        ],
    }


@app.get("/api/strategies/{strategy_id}")
async def strategy_detail(request: Request, strategy_id: str, user: RequiredUser) -> dict[str, Any]:
    await current_account(request.app.state.db, user, required=True)
    result = await request.app.state.engine.run_detail(strategy_id, str(user["id"]))
    return {"success": True, "result": result}


@app.post("/api/strategies", status_code=201)
async def save_strategy(request: Request, body: SaveStrategyRequest, user: RequiredUser) -> dict[str, Any]:
    await current_account(request.app.state.db, user, required=True)
    if body.status == "scheduled":
        raise AppError(422, "Use the exit-choice scheduling endpoint", "exit_choice_required")
    result = await request.app.state.engine.save_strategy(
        str(user["id"]),
        body.strategy,
        body.status,
        str(body.savedStrategyId) if body.savedStrategyId else None,
    )
    return {"success": True, "result": result}


class ExitScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    savedStrategyId: str
    expectedVersion: int
    entryAt: datetime
    exitChoice: ExitChoice


class CommitExitScheduleRequest(ExitScheduleRequest):
    expectedExitUtc: datetime
    expectedContractExpiryUtc: datetime


async def resolve_saved_schedule(
    request: Request, user_id: str, body: ExitScheduleRequest
) -> tuple[dict[str, Any], dict[str, Any]]:
    saved = await request.app.state.engine.saved_strategies(user_id, body.savedStrategyId)
    if not saved or saved[0]["version"] != body.expectedVersion:
        raise AppError(409, "Saved strategy version changed", "saved_strategy_changed")
    template = saved[0]["definition_json"]
    client = await delta_client_for_user(request.app.state.db, settings, user_id)
    try:
        options = await fetch_option_catalog(client, template["instrument"]["underlying"])
    finally:
        await client.close()
    try:
        return resolve_exit_schedule(template, entry_at=body.entryAt, choice=body.exitChoice, options=options)
    except ValueError as error:
        raise AppError(422, str(error), "exit_schedule_invalid") from error


@app.post("/api/strategies/exit-preview")
async def preview_exit_schedule(request: Request, body: ExitScheduleRequest, user: RequiredUser) -> dict[str, Any]:
    await current_account(request.app.state.db, user, required=True)
    definition, schedule = await resolve_saved_schedule(request, str(user["id"]), body)
    return {"success": True, "result": {"definition": definition, "schedule": schedule}}


@app.post("/api/strategies/schedule", status_code=201)
async def schedule_with_exit_choice(
    request: Request, body: CommitExitScheduleRequest, user: RequiredUser
) -> dict[str, Any]:
    await current_account(request.app.state.db, user, required=True)
    definition, schedule = await resolve_saved_schedule(request, str(user["id"]), body)
    if body.expectedExitUtc.astimezone(UTC) != datetime.fromisoformat(
        schedule["exitUtc"]
    ) or body.expectedContractExpiryUtc.astimezone(UTC) != datetime.fromisoformat(schedule["contractExpiryUtc"]):
        raise AppError(409, "Option availability or schedule changed; preview again", "exit_preview_stale")
    current = await request.app.state.engine.saved_strategies(str(user["id"]), body.savedStrategyId)
    if not current or current[0]["version"] != body.expectedVersion:
        raise AppError(409, "Saved strategy version changed", "saved_strategy_changed")
    result = await request.app.state.engine.save_strategy(
        str(user["id"]),
        StrategyDefinition.model_validate(definition),
        "scheduled",
        body.savedStrategyId,
    )
    return {"success": True, "result": {**result, "schedule": schedule}}


@app.post("/api/strategies/preview")
async def preview_strategy(request: Request, body: StrategyDefinition, user: RequiredUser) -> dict[str, Any]:
    await current_account(request.app.state.db, user, required=True)
    client = await delta_client_for_user(request.app.state.db, settings, str(user["id"]))
    try:
        result = await request.app.state.engine.preview_strategy(client, body)
    finally:
        await client.close()
    return {"success": True, **result}


@app.delete("/api/strategies/{strategy_id}")
async def cancel_strategy(request: Request, strategy_id: str, user: RequiredUser) -> dict[str, bool]:
    await request.app.state.engine.cancel_strategy(strategy_id, str(user["id"]))
    return {"success": True}


@app.delete("/api/strategies/{strategy_id}/record")
async def delete_strategy_record(request: Request, strategy_id: str, user: RequiredUser) -> dict[str, bool]:
    """Removes the run from the user's history once settled; the owner ledger keeps a marked copy."""
    await request.app.state.engine.delete_strategy(strategy_id, str(user["id"]))
    return {"success": True}


@app.post("/api/strategies/{strategy_id}/execute")
async def execute_strategy(request: Request, strategy_id: str, user: RequiredUser) -> dict[str, Any]:
    owned = await request.app.state.db.select(
        "strategies",
        {"select": "id", "id": f"eq.{strategy_id}", "user_id": f"eq.{user['id']}", "limit": "1"},
    )
    if not owned:
        raise AppError(404, "Strategy not found", "strategy_not_found")
    return {"success": True, "result": await request.app.state.engine.execute_entry(strategy_id)}


@app.post("/api/strategies/{strategy_id}/exit")
async def exit_strategy(
    request: Request, strategy_id: str, body: ClosePositionRequest, user: RequiredUser
) -> dict[str, Any]:
    owned = await request.app.state.db.select(
        "strategies",
        {"select": "id", "id": f"eq.{strategy_id}", "user_id": f"eq.{user['id']}", "limit": "1"},
    )
    if not owned:
        raise AppError(404, "Strategy not found", "strategy_not_found")
    return {"success": True, "result": await request.app.state.engine.execute_exit(strategy_id)}
