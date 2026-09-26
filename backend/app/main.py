import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from ipaddress import ip_address
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

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
from .config import get_settings
from .delta import DeltaClient
from .engine import Scheduler, TradingEngine
from .errors import AppError
from .instance_lock import InstanceLock
from .models import (
    CancelOrderRequest,
    CapitalSettingsUpdate,
    ClosePositionRequest,
    ConnectRequest,
    SaveStrategyRequest,
    StrategyDefinition,
)
from .news import router as news_router
from .portfolio_stream import serve_portfolio
from .recovery_mirror import RecoveryMirror
from .strategy import delta_expiry
from .supabase import SupabaseAdmin

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    local_pool = None
    if settings.application_storage == "local":
        if settings.trading_writer_enabled:
            await asyncio.to_thread(apply_migrations, settings.local_database_url)
        database_url = (
            settings.local_database_url if settings.trading_writer_enabled else settings.local_reader_database_url
        )
        local_pool = AsyncConnectionPool(database_url, min_size=1, max_size=12, open=False)
        await local_pool.open(wait=True)
    recovery_pending = False
    if local_pool is not None:
        async with local_pool.connection() as connection:
            result = await connection.execute("select pending from trade.recovery_gate where key='main'")
            gate = await result.fetchone()
            recovery_pending = bool(gate and gate[0])
    instance_lock = (
        InstanceLock(settings.trading_lock_path)
        if settings.trading_writer_enabled and settings.convex_runtime_enabled
        else None
    )
    if instance_lock is not None:
        instance_lock.acquire()
    db = SupabaseAdmin(settings, local_pool)
    mirror = (
        RecoveryMirror(local_pool, db.client, settings.convex_url, settings.convex_trading_secret)
        if (settings.application_storage == "local" and settings.trading_writer_enabled
            and settings.recovery_mirror_enabled and not recovery_pending)
        else None
    )
    if mirror is not None:
        mirror.start()
    engine_settings = (
        settings if settings.trading_writer_enabled else settings.model_copy(update={"delta_events_enabled": False})
    )
    engine = TradingEngine(db, engine_settings)
    scheduler = Scheduler(
        engine,
        settings.scheduler_poll_seconds,
        settings.scheduler_enabled and settings.trading_writer_enabled and not recovery_pending,
    )
    automation_scheduler = AutomationScheduler(db, engine)
    app.state.db = db
    app.state.recovery_mirror = mirror
    app.state.recovery_pending = recovery_pending
    app.state.engine = engine
    app.state.scheduler = scheduler
    app.state.automation_scheduler = automation_scheduler
    if settings.trading_writer_enabled and settings.convex_runtime_enabled:
        try:
            response = await db.client.get("https://api.ipify.org", timeout=5)
            response.raise_for_status()
            outbound_ip = str(ip_address(response.text.strip()))
            await db.runtime.data.request("accounts:updateOutboundIp", {"ip": outbound_ip}, mutation=True)
        except Exception:
            logger.exception("Could not refresh the server outbound IP")
    if settings.trading_writer_enabled and not settings.scheduler_enabled and not recovery_pending:
        await engine.recover_interrupted_states()
    scheduler.start()
    if settings.trading_writer_enabled and settings.automation_scheduler_enabled and not recovery_pending:
        automation_scheduler.start()
    try:
        yield
    finally:
        await automation_scheduler.stop()
        await scheduler.stop()
        if mirror is not None:
            await mirror.stop()
        await engine.close()
        await db.close()
        if local_pool is not None:
            await local_pool.close()
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
    app_state = getattr(request.scope.get("app"), "state", None)
    if getattr(app_state, "recovery_pending", False) and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": {
                    "code": "recovery_reconciliation_required",
                    "message": "Trading remains paused until restored state is reconciled with Delta Exchange",
                },
            },
        )
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
app.include_router(news_router)
app.include_router(automation_router)

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
async def local_library(request: Request, user: RequiredUser) -> dict[str, Any]:
    if settings.application_storage != "local":
        raise AppError(404, "Local strategy library unavailable", "library_unavailable")
    rows = await request.app.state.db.local_data.saved_strategies(str(user["id"]))
    return {"success": True, "result": rows}


@app.put("/api/library/{strategy_id}")
async def local_library_save(
    request: Request, strategy_id: str, body: LibrarySave, user: RequiredUser
) -> dict[str, Any]:
    if settings.application_storage != "local" or strategy_id != body.id:
        raise AppError(404, "Local strategy library unavailable", "library_unavailable")
    row = await request.app.state.db.local_data.library_save(str(user["id"]), body.model_dump())
    return {"success": True, "result": request.app.state.db.local_data._legacy_library_row(row)}


@app.delete("/api/library/{strategy_id}")
async def local_library_remove(
    request: Request, strategy_id: str, expectedVersion: int, user: RequiredUser
) -> dict[str, bool]:
    if settings.application_storage != "local":
        raise AppError(404, "Local strategy library unavailable", "library_unavailable")
    await request.app.state.db.local_data.library_remove(str(user["id"]), strategy_id, expectedVersion)
    return {"success": True}


@app.get("/api/revisions")
async def local_revisions(request: Request, user: RequiredUser) -> StreamingResponse:
    if settings.application_storage != "local":
        raise AppError(404, "Local revisions unavailable", "revisions_unavailable")
    pool: AsyncConnectionPool = request.app.state.db.runtime.pool
    user_id = str(user["id"])

    async def events():
        async with pool.connection() as connection:
            result = await connection.execute("select coalesce(max(id),0) from trade.recovery_outbox")
            cursor = (await result.fetchone())[0]
        yield ": connected\n\n"
        while not await request.is_disconnected():
            async with pool.connection() as connection:
                result = await connection.execute(
                    """select id,entity_type,payload from trade.recovery_outbox
                       where id>%s and entity_type in ('strategies','analysis_jobs')
                       order by id limit 100""",
                    (cursor,),
                )
                rows = await result.fetchall()
            revisions: dict[str, int] = {}
            for event_id, entity_type, payload in rows:
                cursor = event_id
                owner = payload.get("owner_id") if isinstance(payload, dict) else None
                if owner in {user_id, "global"} or payload is None:
                    field = "strategies" if entity_type == "strategies" else "automation"
                    revisions[field] = event_id
            if revisions:
                yield f"data: {json.dumps(revisions)}\n\n"
            elif not rows:
                yield ": alive\n\n"
            await asyncio.sleep(2 if len(rows) < 100 else 0)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/internal/research")
async def local_research_operation(
    request: Request, body: ResearchOperation, x_analysis_secret: str | None = Header(default=None)
) -> dict[str, Any]:
    if settings.application_storage != "local" or not settings.analysis_service_secret:
        raise AppError(404, "Research operation unavailable", "research_operation_unavailable")
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


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    scheduler: Scheduler = request.app.state.scheduler
    mirror: RecoveryMirror | None = request.app.state.recovery_mirror
    return {
        "success": True,
        "service": "delta-strategy-api",
        "scheduler": scheduler.status(),
        "recoveryPending": request.app.state.recovery_pending,
        "recoveryMirrorEnabled": settings.application_storage == "local" and settings.recovery_mirror_enabled,
        **({"recoveryMirror": await mirror.status()} if mirror is not None else {}),
    }


@app.get("/api/session")
async def session(request: Request, user: OptionalUser) -> dict[str, Any]:
    db: SupabaseAdmin = request.app.state.db
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
    if settings.application_storage == "local":
        await request.app.state.engine.invalidate_credentials(str(user["id"]))
    return {"success": True, "account": result["account"]}


@app.delete("/api/session")
async def disconnect_delta(request: Request, user: RequiredUser) -> dict[str, bool]:
    await remove_connection(request.app.state.db, str(user["id"]))
    if settings.application_storage == "local":
        await request.app.state.engine.invalidate_credentials(str(user["id"]))
    return {"success": True}


@app.get("/api/account/overview")
async def account_overview(request: Request, user: RequiredUser) -> dict[str, Any]:
    db: SupabaseAdmin = request.app.state.db
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
    result = await request.app.state.engine.save_strategy(
        str(user["id"]),
        body.strategy,
        body.status,
        str(body.savedStrategyId) if body.savedStrategyId else None,
    )
    return {"success": True, "result": result}


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
    """Erases the run and its execution history. Only allowed once it is settled."""
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
