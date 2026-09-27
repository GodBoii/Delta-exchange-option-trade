"""Personal P&L and the owner Users section.

Every ``/api/owner`` route re-checks ``user_type = 'owner'`` on the request. Personal
routes read only the caller's own rows and never rows the caller deleted. Responses
carry allowed profile, connection, capital and ledger fields only.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from .auth import require_owner, require_user
from .automation import set_account_automation
from .database import Database
from .errors import AppError
from .owner_ledger import DeletedFilter, OwnerLedger

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reporting"])

RangeKey = Literal["7d", "30d", "90d", "1y", "all"]
StateFilter = Literal["settled", "open", "scheduled", "cancelled", "incomplete", "attention"]
RANGES = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90), "1y": timedelta(days=365)}
OWNER_ROLE_MAX_AGE_SECONDS = 10.0
WALLET_TIMEOUT_SECONDS = 8.0
WALLET_CACHE_SECONDS = 30.0
WALLET_CONCURRENCY = 3
SEARCH_UNSAFE = re.compile(r"[^\w@.+\- ]")


async def require_owner_user(
    request: Request, user: Annotated[dict[str, Any], Depends(require_user)]
) -> dict[str, Any]:
    await require_owner(request.app.state.db, user, max_age=OWNER_ROLE_MAX_AGE_SECONDS)
    return user


RequiredUser = Annotated[dict[str, Any], Depends(require_user)]
OwnerUser = Annotated[dict[str, Any], Depends(require_owner_user)]
RangeQuery = Annotated[RangeKey, Query(alias="range")]
PageLimit = Annotated[int, Query(ge=1, le=50)]


class AutomationSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@dataclass
class WalletProbe:
    """Bounded, briefly cached live wallet reads for owner views."""

    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(WALLET_CONCURRENCY))
    cache: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)

    async def read(self, request: Request, user_id: str) -> dict[str, Any]:
        cached = self.cache.get(user_id)
        if cached and time.monotonic() - cached[0] < WALLET_CACHE_SECONDS:
            return cached[1]
        engine = request.app.state.engine
        async with self.semaphore:
            try:
                client = await engine.client_for_user(user_id)
                try:
                    available, total = await asyncio.wait_for(engine.usd_capital(client), WALLET_TIMEOUT_SECONDS)
                finally:
                    await client.close()
            except (AppError, httpx.HTTPError, TimeoutError, KeyError, ValueError) as error:
                code = error.code if isinstance(error, AppError) else type(error).__name__
                logger.warning("Owner wallet read unavailable user=%s code=%s", user_id, code)
                result = {"state": "unavailable", "reason": code, "observedAt": None}
                self.cache[user_id] = (time.monotonic(), result)
                return result
        observed = datetime.now(UTC)
        result = {
            "state": "live",
            "totalBalance": str(total),
            "availableBalance": str(available),
            "observedAt": observed.isoformat(),
        }
        self.cache[user_id] = (time.monotonic(), result)
        db: Database = request.app.state.db
        if getattr(db.settings, "trading_writer_enabled", True):
            try:
                await db.runtime.ledger.record_wallet(user_id, total, available, observed)
            except Exception:
                logger.exception("Could not record a wallet observation user=%s", user_id)
        return result


def _ledger(request: Request) -> OwnerLedger:
    return request.app.state.db.runtime.ledger


def _probe(request: Request) -> WalletProbe:
    probe = getattr(request.app.state, "wallet_probe", None)
    if probe is None:
        probe = WalletProbe()
        request.app.state.wallet_probe = probe
    return probe


def _since(range_key: str) -> datetime | None:
    window = RANGES.get(range_key)
    return datetime.now(UTC) - window if window else None


def _user_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise AppError(404, "User not found", "user_not_found") from error


def trade_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": row["run_id"],
        "name": row["strategy_name"],
        "asset": row["asset"],
        "status": row["status"],
        "accountingState": row["accounting_state"],
        "exclusionReason": row["exclusion_reason"],
        "createdAt": row["created_at"],
        "entryAt": row["entry_at"],
        "exitAt": row["exit_at"],
        "entryExecutedAt": row["entry_executed_at"],
        "exitExecutedAt": row["exit_executed_at"],
        "activityAt": row["activity_at"],
        "realizedPnl": row["realized_pnl"],
        "grossPnl": row["gross_pnl"],
        "exchangeFees": row["exchange_fees"],
        "capitalBudget": row["capital_budget"],
        "walletTotalAtEntry": row["wallet_total_at_entry"],
        "walletAvailableAtEntry": row["wallet_available_at_entry"],
        "deletedByUserAt": row["deleted_by_user_at"],
        "capturedAt": row["last_captured_at"],
    }


async def _history(ledger: OwnerLedger) -> dict[str, Any]:
    status = await ledger.history_status()
    return {"historyComplete": status["complete"], "historyVerifiedAt": status["verifiedAt"]}


@router.get("/api/me/pnl")
async def my_pnl(request: Request, user: RequiredUser, range_key: RangeQuery = "all") -> dict[str, Any]:
    ledger = _ledger(request)
    summary = await ledger.summary(str(user["id"]), deleted="exclude", since=_since(range_key))
    return {
        "success": True,
        "scope": "personal",
        "range": range_key,
        "asOf": datetime.now(UTC).isoformat(),
        **await _history(ledger),
        "summary": summary,
    }


@router.get("/api/me/trades")
async def my_trades(
    request: Request,
    user: RequiredUser,
    range_key: RangeQuery = "all",
    state: StateFilter | None = None,
    cursor: str | None = None,
    limit: PageLimit = 25,
) -> dict[str, Any]:
    page = await _ledger(request).trades(
        str(user["id"]), deleted="exclude", since=_since(range_key), state=state, cursor=cursor, limit=limit
    )
    return {"success": True, "items": [trade_item(row) for row in page["items"]], "nextCursor": page["nextCursor"]}


def _profile_fields(profile: dict[str, Any], owner_id: str) -> dict[str, Any]:
    return {
        "id": profile["id"],
        "displayName": profile.get("display_name"),
        "email": profile.get("email"),
        "phoneNumber": profile.get("phone_number"),
        "avatarUrl": profile.get("avatar_url"),
        "userType": "owner" if profile.get("user_type") == "owner" else "user",
        "registeredAt": profile.get("created_at"),
        "isCurrentUser": profile["id"] == owner_id,
    }


def _account_fields(account: dict[str, Any] | None) -> dict[str, Any]:
    account = account or {}
    return {
        "initialized": bool(account),
        "connectionStatus": account.get("connectionStatus") or "not_connected",
        "accountName": account.get("accountName"),
        "email": account.get("email"),
        "deltaAccountId": account.get("deltaAccountId"),
    }


def _wallet_fields(observation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not observation:
        return None
    return {
        "totalBalance": observation["total_balance"],
        "availableBalance": observation["available_balance"],
        "observedAt": observation["observed_at"],
        "source": observation["source"],
    }


@router.get("/api/owner/users")
async def owner_users(
    request: Request,
    owner: OwnerUser,
    search: Annotated[str | None, Query(max_length=80)] = None,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: PageLimit = 20,
) -> dict[str, Any]:
    db: Database = request.app.state.db
    ledger = _ledger(request)
    needle = SEARCH_UNSAFE.sub("", search or "").strip().replace(" ", "*") or None
    profiles, matching = await db.registered_profiles(offset=offset, limit=limit, search=needle)
    registered = matching if needle is None else (await db.registered_profiles(offset=0, limit=1))[1]
    ids = [profile["id"] for profile in profiles]
    accounts, rollups, wallets, counts, attention = await asyncio.gather(
        db.local_data.account_rows(ids),
        ledger.rollups(ids),
        ledger.latest_wallets(ids),
        db.local_data.account_counts(),
        ledger.accounts_with_attention(),
    )
    if getattr(db.settings, "trading_writer_enabled", True):
        try:
            await ledger.sync_profiles(profiles, accounts)
        except Exception:
            logger.exception("Could not refresh owner profile snapshots")
    items = []
    for profile in profiles:
        rollup = rollups.get(profile["id"]) or {}
        account = accounts.get(profile["id"])
        items.append(
            {
                **_profile_fields(profile, str(owner["id"])),
                "account": _account_fields(account),
                "automationEnabled": bool(account and account["automationEnabled"]),
                "performance": {
                    "netRealizedPnl": rollup.get("net_realized", "0"),
                    "settledRuns": rollup.get("settled_runs", 0),
                    "totalRuns": rollup.get("total_runs", 0),
                    "openRuns": rollup.get("open_runs", 0),
                    "attentionRuns": rollup.get("attention_runs", 0),
                    "lastActivityAt": rollup.get("last_activity_at"),
                },
                "lastRecordedWallet": _wallet_fields(wallets.get(profile["id"])),
            }
        )
    return {
        "success": True,
        "asOf": datetime.now(UTC).isoformat(),
        "summary": {
            "registeredUsers": registered,
            "connectedAccounts": counts["connected"],
            "automationEnabledAccounts": counts["automation"],
            "accountsWithAttentionRuns": attention,
            "automationWithoutConnection": counts["automation_without_connection"],
        },
        "matching": matching,
        "offset": offset,
        "limit": limit,
        "items": items,
    }


async def _registered(db: Database, user_id: str) -> dict[str, Any]:
    profile = await db.registered_profile(user_id)
    if profile is None:
        raise AppError(404, "User not found", "user_not_found")
    return profile


@router.get("/api/owner/users/{user_id}")
async def owner_user_detail(
    request: Request, user_id: str, owner: OwnerUser, range_key: RangeQuery = "all"
) -> dict[str, Any]:
    db: Database = request.app.state.db
    ledger = _ledger(request)
    target = _user_id(user_id)
    profile = await _registered(db, target)
    since = _since(range_key)
    account = (await db.local_data.account_rows([target])).get(target)
    connected = bool(account and account["connectionStatus"] == "connected")
    # Read the wallet first so a fresh observation appears in the capital history below.
    wallet = await _probe(request).read(request, target) if connected else {"state": "not_connected"}
    owner_scope, user_scope, history, capital = await asyncio.gather(
        ledger.summary(target, deleted="include", since=since),
        ledger.summary(target, deleted="exclude", since=since),
        _history(ledger),
        ledger.capital_history(target, 30),
    )
    return {
        "success": True,
        "asOf": datetime.now(UTC).isoformat(),
        "range": range_key,
        **history,
        "profile": _profile_fields(profile, str(owner["id"])),
        "account": _account_fields(account),
        "automation": {"enabled": bool(account and account["automationEnabled"])},
        "capitalPolicy": {
            "allocationMode": account["allocationMode"] if account else None,
            "capitalAmount": account["capitalAmount"] if account else None,
        },
        "wallet": wallet,
        "capitalHistory": [
            {
                "kind": item["kind"],
                "source": item["source"],
                "observedAt": item["observed_at"],
                "runId": item["run_id"],
                "totalBalance": item["total_balance"],
                "availableBalance": item["available_balance"],
                "allocationMode": item["allocation_mode"],
                "capitalAmount": item["capital_amount"],
                "allocatedBudget": item["allocated_budget"],
            }
            for item in capital
        ],
        "performance": {"ownerScope": owner_scope, "userScope": user_scope},
    }


@router.get("/api/owner/users/{user_id}/trades")
async def owner_user_trades(
    request: Request,
    user_id: str,
    _owner: OwnerUser,
    range_key: RangeQuery = "all",
    state: StateFilter | None = None,
    deleted: DeletedFilter = "include",
    cursor: str | None = None,
    limit: PageLimit = 25,
) -> dict[str, Any]:
    page = await _ledger(request).trades(
        _user_id(user_id), deleted=deleted, since=_since(range_key), state=state, cursor=cursor, limit=limit
    )
    return {"success": True, "items": [trade_item(row) for row in page["items"]], "nextCursor": page["nextCursor"]}


@router.get("/api/owner/users/{user_id}/trades/{run_id}")
async def owner_user_trade(request: Request, user_id: str, run_id: str, _owner: OwnerUser) -> dict[str, Any]:
    target = _user_id(user_id)
    row = await _ledger(request).trade(target, run_id)
    if row is None:
        raise AppError(404, "Trade not found", "trade_not_found")
    detail, source = row["detail"], "archive"
    if row["deleted_by_user_at"] is None:
        try:
            detail = await request.app.state.engine.run_detail(run_id, target, include_raw=False)
            source = "live"
        except AppError as error:
            if error.code != "strategy_not_found":
                raise
    return {
        "success": True,
        "source": source,
        "trade": trade_item(row),
        "firstCapturedAt": row["first_captured_at"],
        "run": detail,
    }


@router.put("/api/owner/users/{user_id}/automation")
async def owner_user_automation(
    request: Request, user_id: str, body: AutomationSwitch, owner: OwnerUser
) -> dict[str, Any]:
    db: Database = request.app.state.db
    target = _user_id(user_id)
    await _registered(db, target)
    saved = await set_account_automation(db, target, body.enabled, actor_id=str(owner["id"]))
    return {
        "success": True,
        "automation": {"enabled": bool(saved["enabled"])},
        "savedAt": datetime.now(UTC).isoformat(),
    }
