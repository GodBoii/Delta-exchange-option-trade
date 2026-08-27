import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import create_connection, current_account, delta_client_for_user, optional_user, require_user
from .automation import AutomationScheduler
from .automation import router as automation_router
from .capital import capital_budget, maximum_concurrent_strategies
from .config import get_settings
from .delta import DeltaClient
from .engine import Scheduler, TradingEngine
from .errors import AppError
from .models import (
    CancelOrderRequest,
    CapitalSettingsUpdate,
    ClosePositionRequest,
    ConnectRequest,
    SaveStrategyRequest,
    StrategyDefinition,
)
from .news import router as news_router
from .strategy import delta_expiry
from .supabase import SupabaseAdmin

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SupabaseAdmin(settings)
    engine = TradingEngine(db, settings)
    scheduler = Scheduler(engine, settings.scheduler_poll_seconds, settings.scheduler_enabled)
    automation_scheduler = AutomationScheduler(db, engine)
    app.state.db = db
    app.state.engine = engine
    app.state.scheduler = scheduler
    app.state.automation_scheduler = automation_scheduler
    scheduler.start()
    automation_scheduler.start()
    try:
        yield
    finally:
        await automation_scheduler.stop()
        await scheduler.stop()
        await db.close()


app = FastAPI(
    title="Delta Strategy Desk API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)
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
    return {"success": True, "service": "delta-strategy-api", "scheduler": scheduler.status()}


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
    return {"success": True, "account": result["account"]}


@app.delete("/api/session")
async def disconnect_delta(request: Request, user: RequiredUser) -> dict[str, bool]:
    await request.app.state.db.rpc("delete_delta_connection", {"p_user_id": str(user["id"])})
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
    payload = {
        "user_id": str(user["id"]),
        "allocation_mode": body.allocationMode,
        "capital_amount": body.capitalAmount if body.allocationMode == "fixed_amount" else None,
    }
    rows = await request.app.state.db.upsert("capital_settings", payload, on_conflict="user_id")
    if not rows:
        raise AppError(500, "Could not save the capital policy", "capital_settings_failed")
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
            "select": ("id,name,status,entry_at,exit_at,entry_execution_at,exit_execution_at,last_error,created_at"),
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
