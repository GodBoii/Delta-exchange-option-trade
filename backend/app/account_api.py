"""Authenticated Profile controls. Exchange secrets never appear in responses."""

import asyncio
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg.rows import dict_row
from pydantic import Field

from .account_funding import usd_wallet
from .account_manager import AccountManager
from .auth import require_user
from .errors import AppError
from .models import ConnectRequest, StrictModel
from .server_ip import ServerIp

router = APIRouter(prefix="/api/profile", tags=["Profile accounts"])
User = Annotated[dict[str, Any], Depends(require_user)]


def manager(request: Request) -> AccountManager:
    value = request.app.state.engine.subaccounts
    if value is None:
        raise AppError(503, "Subaccount storage is unavailable", "subaccount_storage_unavailable")
    return value


class ManagementUpdate(StrictModel):
    mode: Literal["main", "automatic", "manual"]
    autoFund: bool
    expectedRevision: int = Field(ge=0)


class ParticipationUpdate(StrictModel):
    participation: Literal["disabled", "manual", "automatic"]


class AssignmentRequest(StrictModel):
    strategyId: str = Field(min_length=1, max_length=100)
    accountId: str = Field(pattern=r"^\d{1,30}$")


class TransferRequest(StrictModel):
    requestId: UUID
    sourceId: str = Field(pattern=r"^\d{1,30}$")
    targetId: str = Field(pattern=r"^\d{1,30}$")
    amount: Decimal = Field(gt=0, max_digits=28, decimal_places=10, allow_inf_nan=False)
    confirm: Literal[True]


@router.get("/server-ip")
async def server_ip(request: Request, user: User) -> dict[str, Any]:
    return {"success": True, **await ServerIp(request.app.state.db).read()}


@router.post("/server-ip/refresh")
async def refresh_ip(request: Request, user: User) -> dict[str, Any]:
    return {"success": True, **await request.app.state.server_ip.refresh()}


@router.get("/subaccounts")
async def overview(request: Request, user: User) -> dict[str, Any]:
    accounts = manager(request)
    owner = str(user["id"])
    main = await accounts.registry.main(owner)
    config, rows, transfers = await asyncio.gather(
        accounts.registry.settings(owner), accounts.registry.accounts(owner), accounts.funding.records(owner)
    )
    parent = str(main["delta_user_id"])
    policy = await accounts.engine.capital_policy(owner)
    ready = sum(
        row["present"]
        and row["connected"]
        and row["participation"] == "automatic"
        and row["parent_id"] == parent
        and not row["busy"]
        for row in rows
    )
    return {
        "success": True,
        "mainAccount": {"id": parent, "name": main["account_name"]},
        "settings": {"mode": config["mode"], "autoFund": config["auto_fund"], "revision": config["revision"]},
        "accounts": [
            {
                "id": row["account_id"],
                "name": row["account_name"],
                "parentId": row["parent_id"],
                "marginMode": row["margin_mode"],
                "present": row["present"],
                "connected": row["connected"],
                "busy": row["busy"],
                "participation": row["participation"],
                "checkedAt": row["checked_at"],
                "strategyId": row["strategy_id"],
                "strategyName": row["strategy_name"],
                "budget": str(row["budget"]) if row["budget"] else None,
            }
            for row in rows
        ],
        "capacity": {"available": ready, "target": policy.concurrency_limit()},
        "transfers": transfers,
    }


@router.post("/subaccounts/sync")
async def sync(request: Request, user: User) -> dict[str, Any]:
    accounts = manager(request)
    owner = str(user["id"])
    client = await accounts.engine.client_for_user(owner)
    try:
        await accounts.registry.sync(owner, await client.request("GET", "/v2/sub_accounts", authenticated=True))
    finally:
        await client.close()
    return await overview(request, user)


@router.put("/subaccounts/settings")
async def settings(request: Request, body: ManagementUpdate, user: User) -> dict[str, Any]:
    accounts = manager(request)
    await accounts.registry.save_settings(str(user["id"]), body.mode, body.autoFund, body.expectedRevision)
    return await overview(request, user)


@router.put("/subaccounts/{account_id}/key")
async def save_key(request: Request, account_id: str, body: ConnectRequest, user: User) -> dict[str, Any]:
    accounts = manager(request)
    owner = str(user["id"])
    # Re-prove current parent membership before accepting a secret.
    await sync(request, user)
    await accounts.registry.save_key(owner, account_id, body.apiKey, body.apiSecret)
    await accounts.engine.invalidate_credentials(owner)
    return {"success": True}


@router.delete("/subaccounts/{account_id}/key")
async def remove_key(request: Request, account_id: str, user: User) -> dict[str, Any]:
    accounts = manager(request)
    owner = str(user["id"])
    await accounts.funding.flat(owner, account_id)
    await accounts.registry.remove_key(owner, account_id)
    await accounts.engine.invalidate_credentials(owner)
    return {"success": True}


@router.put("/subaccounts/{account_id}/participation")
async def participation(request: Request, account_id: str, body: ParticipationUpdate, user: User) -> dict[str, Any]:
    await manager(request).registry.set_participation(str(user["id"]), account_id, body.participation)
    return {"success": True}


@router.get("/subaccounts/{account_id}/snapshot")
async def snapshot(request: Request, account_id: str, user: User) -> dict[str, Any]:
    accounts = manager(request)
    owner = str(user["id"])
    await accounts.registry.row(owner, account_id)
    client = await accounts.engine.client_for_user(owner, exchange_account_id=account_id)
    try:
        wallet, exposure = await asyncio.gather(client.balances(), accounts.engine.account_exposure(client))
        available, total = usd_wallet(wallet)
        async with accounts.registry.pool.connection() as connection:
            result = await connection.execute(
                """select sum(realized_pnl) from owner_reporting.trade_ledger
                   where owner_user_id=%s and detail->>'exchangeAccountId'=%s
                   and accounting_state='settled' and deleted_by_user_at is null""",
                (owner, account_id),
            )
            realized = (await result.fetchone())[0]
        positions = (await client.positions())["result"]
        fields = ("product_id", "product_symbol", "size", "entry_price", "mark_price", "unrealized_pnl")
        order_fields = ("id", "product_id", "product_symbol", "side", "size", "unfilled_size", "state", "reduce_only")
        return {
            "success": True,
            "available": str(available),
            "balance": str(total),
            "realizedPnl": str(realized) if realized is not None else None,
            "positions": [{key: row.get(key) for key in fields} for row in positions],
            "orders": [{key: row.get(key) for key in order_fields} for row in exposure.open_orders],
        }
    finally:
        await client.close()


@router.post("/subaccounts/assignments")
async def assign(request: Request, body: AssignmentRequest, user: User) -> dict[str, Any]:
    accounts = manager(request)
    owner = str(user["id"])
    await accounts.funding.flat(owner, body.accountId)
    await accounts.registry.reserve(owner, body.strategyId, body.accountId)
    return {"success": True}


@router.post("/subaccounts/assignments/{strategy_id}/release")
async def release(request: Request, strategy_id: str, user: User) -> dict[str, Any]:
    accounts = manager(request)
    owner = str(user["id"])
    async with accounts.registry.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("select status from trade.strategies where id=%s and owner_id=%s", (strategy_id, owner))
        row = await cursor.fetchone()
    if not row or row["status"] not in {"completed", "cancelled", "skipped"}:
        raise AppError(409, "Finish or cancel the run before releasing its account", "subaccount_busy")
    await accounts.release(owner, strategy_id)
    return {"success": True}


@router.post("/subaccounts/transfers")
async def transfer(request: Request, body: TransferRequest, user: User) -> dict[str, Any]:
    return {
        "success": True,
        **await manager(request).funding.transfer(
            str(user["id"]), body.requestId, body.sourceId, body.targetId, body.amount
        ),
    }


@router.get("/subaccounts/transfers/history")
async def transfer_history(request: Request, user: User, after: str | None = Query(default=None, max_length=500)):
    return {"success": True, **await manager(request).funding.history(str(user["id"]), after)}


@router.post("/subaccounts/transfers/{identity}/reconcile")
async def reconcile(request: Request, identity: UUID, user: User) -> dict[str, Any]:
    return {"success": True, **await manager(request).funding.reconcile(str(user["id"]), identity)}
