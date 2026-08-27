from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from .auth import current_account, require_user
from .engine import TradingEngine, iso_now
from .errors import AppError
from .supabase import SupabaseAdmin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/automation", tags=["automation"])
news_analyzer_url = "http://news-analyzer:8002"
MODEL_ID = "deepseek/deepseek-v4-flash-vision-exp"

RequiredUser = Annotated[dict[str, Any], Depends(require_user)]


class AutomationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class AutomationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


async def ensure_settings(db: SupabaseAdmin, user_id: str) -> dict[str, Any]:
    rows = await db.select("automation_settings", {"select": "*", "user_id": f"eq.{user_id}", "limit": "1"})
    if rows:
        return rows[0]
    inserted = await db.upsert(
        "automation_settings",
        {"user_id": user_id, "enabled": False, "model_id": MODEL_ID},
        on_conflict="user_id",
    )
    if not inserted:
        raise AppError(500, "Could not initialize automation settings", "automation_settings_failed")
    return inserted[0]


async def build_account_context(engine: TradingEngine, user_id: str) -> dict[str, Any]:
    client = await engine.client_for_user(user_id)
    try:
        orders, positions, active = await asyncio.gather(
            client.open_orders(),
            client.positions(),
            engine.db.select(
                "strategies",
                {
                    "select": "id,name,status,saved_strategy_id,risk_state,entry_at,exit_at",
                    "user_id": f"eq.{user_id}",
                    "status": "in.(scheduled,executing_entry,active,executing_exit,attention)",
                    "limit": "25",
                },
            ),
        )
    finally:
        await client.close()
    return {
        "openOrders": [
            _pick(
                row,
                "id",
                "product_id",
                "product_symbol",
                "side",
                "size",
                "unfilled_size",
                "order_type",
                "limit_price",
                "state",
                "created_at",
            )
            for row in orders.get("result") or []
        ],
        "positions": [
            _pick(
                row,
                "product_id",
                "product_symbol",
                "size",
                "entry_price",
                "margin",
                "liquidation_price",
                "realized_pnl",
            )
            for row in positions.get("result") or []
        ],
        "activeStrategies": active,
        "maximumConcurrentStrategies": 3,
    }


async def execute_automation_run(
    *,
    db: SupabaseAdmin,
    engine: TradingEngine,
    user_id: str,
    run_id: str,
    session_id: str,
    reason: str | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        account_context = await build_account_context(engine, user_id)
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                f"{news_analyzer_url}/v1/automation/analyze",
                json={
                    "userId": user_id,
                    "agentRunId": run_id,
                    "sessionId": session_id,
                    "accountContext": account_context,
                    "triggerReason": reason,
                },
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise AppError(
                502, "Automation service returned an invalid response", "invalid_automation_response"
            ) from error
        if response.is_error:
            nested = payload.get("error") if isinstance(payload, dict) else None
            message = nested.get("message") if isinstance(nested, dict) else None
            raise AppError(response.status_code, message or "Automation analysis failed", "automation_agent_failed")
        await db.update(
            "automation_agent_runs",
            {
                "status": "completed",
                "outcome": payload.get("outcome") or "no_trade_for_current_window",
                "completed_at": iso_now(),
                "market_snapshot_id": payload.get("marketSnapshotId"),
                "agno_session_id": payload.get("sessionId"),
                "agno_run_id": payload.get("runId"),
                "report_markdown": payload.get("report"),
                "member_responses": payload.get("memberResponses") or [],
                "tool_calls": payload.get("toolCalls") or [],
                "error": None,
            },
            {"id": f"eq.{run_id}", "user_id": f"eq.{user_id}"},
        )
        logger.info(
            "Automation run completed run_id=%s user_id=%s outcome=%s elapsed_ms=%d",
            run_id,
            user_id,
            payload.get("outcome"),
            round((time.perf_counter() - started) * 1_000),
        )
        return payload
    except Exception as error:
        await db.update(
            "automation_agent_runs",
            {"status": "failed", "completed_at": iso_now(), "error": str(error)},
            {"id": f"eq.{run_id}", "user_id": f"eq.{user_id}"},
        )
        raise


async def _signed_chart(db: SupabaseAdmin, chart: dict[str, Any]) -> dict[str, str] | None:
    bucket = chart.get("bucket")
    path = chart.get("path")
    if not isinstance(bucket, str) or not isinstance(path, str):
        return None
    try:
        url = await db.signed_storage_url(bucket, path)
    except AppError as error:
        logger.warning("Could not sign automation chart path=%s: %s", path, error.message)
        return None
    return {
        "id": str(chart.get("id") or path),
        "label": str(chart.get("label") or "Market chart"),
        "altText": str(chart.get("altText") or chart.get("label") or "Market chart"),
        "url": url,
    }


@router.get("/overview")
async def automation_overview(request: Request, user: RequiredUser) -> dict[str, Any]:
    db: SupabaseAdmin = request.app.state.db
    user_id = str(user["id"])
    settings, strategies, runs, proposals = await asyncio.gather(
        ensure_settings(db, user_id),
        db.select(
            "saved_strategies",
            {
                "select": "id,name,version,enabled_for_ai",
                "or": f"(user_id.eq.{user_id},user_id.is.null)",
                "order": "name.asc",
            },
        ),
        db.select(
            "automation_agent_runs",
            {
                "select": (
                    "id,trigger,status,outcome,scheduled_for,started_at,completed_at,model_id,"
                    "agno_session_id,agno_run_id,market_snapshot_id,report_markdown,error"
                ),
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": "20",
            },
        ),
        db.select(
            "strategy_proposals",
            {
                "select": (
                    "id,saved_strategy_id,saved_strategy_version,status,activation_time,"
                    "proposal_expiry,ai_confidence,reasoning_summary"
                ),
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": "20",
            },
        ),
    )
    names = {row["id"]: row["name"] for row in strategies}
    snapshot_ids = [str(row["market_snapshot_id"]) for row in runs if row.get("market_snapshot_id")]
    snapshots = (
        await db.select(
            "automation_market_snapshots",
            {
                "select": "id,market_json",
                "id": f"in.({','.join(snapshot_ids)})",
                "user_id": f"eq.{user_id}",
            },
        )
        if snapshot_ids
        else []
    )
    stored_charts = {
        str(snapshot["id"]): (snapshot.get("market_json") or {}).get("chartImages") or [] for snapshot in snapshots
    }
    charts_by_snapshot: dict[str, list[dict[str, str]]] = {}
    for snapshot_id, chart_rows in stored_charts.items():
        signed = await asyncio.gather(*(_signed_chart(db, chart) for chart in chart_rows if isinstance(chart, dict)))
        charts_by_snapshot[snapshot_id] = [chart for chart in signed if chart is not None]
    return {
        "success": True,
        "settings": {
            "enabled": settings["enabled"],
            "model": settings["model_id"],
        },
        "enabledStrategies": sum(bool(row["enabled_for_ai"]) for row in strategies),
        "totalStrategies": len(strategies),
        "runs": [
            {
                "id": row["id"],
                "sessionId": row.get("agno_session_id"),
                "runId": row.get("agno_run_id"),
                "trigger": row["trigger"],
                "status": row["status"],
                "outcome": row.get("outcome"),
                "scheduledFor": row["scheduled_for"],
                "startedAt": row.get("started_at"),
                "completedAt": row.get("completed_at"),
                "model": row["model_id"],
                "report": row.get("report_markdown"),
                "charts": charts_by_snapshot.get(str(row.get("market_snapshot_id") or ""), []),
                "error": row.get("error"),
            }
            for row in runs
        ],
        "proposals": [
            {
                "id": row["id"],
                "strategyName": names.get(row["saved_strategy_id"], "Saved strategy"),
                "strategyVersion": row["saved_strategy_version"],
                "status": row["status"],
                "activationTime": row["activation_time"],
                "expiresAt": row["proposal_expiry"],
                "confidence": float(row["ai_confidence"]),
                "reasoning": row["reasoning_summary"],
            }
            for row in proposals
        ],
    }


@router.put("/settings")
async def update_automation_settings(
    request: Request, body: AutomationSettingsUpdate, user: RequiredUser
) -> dict[str, Any]:
    db: SupabaseAdmin = request.app.state.db
    user_id = str(user["id"])
    rows = await db.upsert(
        "automation_settings",
        {
            "user_id": user_id,
            "enabled": body.enabled,
            "model_id": MODEL_ID,
        },
        on_conflict="user_id",
    )
    if not body.enabled:
        pending_proposals = await db.select(
            "strategy_proposals",
            {
                "select": "id,strategy_id",
                "user_id": f"eq.{user_id}",
                "status": "eq.scheduled",
            },
        )
        await asyncio.gather(
            db.update(
                "automation_agent_runs",
                {"status": "cancelled", "completed_at": iso_now(), "error": "Automation was turned off"},
                {"user_id": f"eq.{user_id}", "status": "eq.scheduled"},
            ),
            *(
                db.update(
                    "strategies",
                    {"status": "cancelled", "last_error": "Automation was turned off before entry"},
                    {"id": f"eq.{proposal['strategy_id']}", "status": "eq.scheduled"},
                )
                for proposal in pending_proposals
                if proposal.get("strategy_id")
            ),
            *(
                db.update(
                    "strategy_proposals",
                    {"status": "cancelled", "rejection_reason": "Automation was turned off before entry"},
                    {"id": f"eq.{proposal['id']}", "status": "eq.scheduled"},
                )
                for proposal in pending_proposals
            ),
        )
    return {"success": True, "settings": rows[0] if rows else None}


@router.post("/run")
async def run_automation(request: Request, body: AutomationRunRequest, user: RequiredUser) -> dict[str, Any]:
    db: SupabaseAdmin = request.app.state.db
    engine: TradingEngine = request.app.state.engine
    user_id = str(user["id"])
    await current_account(db, user, required=True)
    settings = await ensure_settings(db, user_id)
    if not settings["enabled"]:
        raise AppError(409, "Turn on Automation before running the agent", "automation_disabled")
    rows = await db.insert(
        "automation_agent_runs",
        {
            "user_id": user_id,
            "trigger": "manual",
            "status": "running",
            "scheduled_for": iso_now(),
            "started_at": iso_now(),
            "model_id": MODEL_ID,
            "reason": body.reason or "Manual automation review",
        },
    )
    if not rows:
        raise AppError(500, "Could not create the automation run", "automation_run_create_failed")
    run_id = str(rows[0]["id"])
    payload = await execute_automation_run(
        db=db,
        engine=engine,
        user_id=user_id,
        run_id=run_id,
        session_id=f"run-{run_id}",
        reason=body.reason,
    )
    return {"success": True, **payload}


class AutomationScheduler:
    def __init__(self, db: SupabaseAdmin, engine: TradingEngine, poll_seconds: float = 30.0) -> None:
        self.db = db
        self.engine = engine
        self.poll_seconds = max(10.0, poll_seconds)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="automation-agent-scheduler")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.task, timeout=5)

    async def run(self) -> None:
        logger.info("Automation scheduler started; polling every %.1f seconds", self.poll_seconds)
        while not self.stop_event.is_set():
            try:
                await self._enqueue_session_reviews()
                await self._process_due_runs()
            except Exception:
                logger.exception("Automation scheduler polling cycle failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.poll_seconds)

    async def _enqueue_session_reviews(self) -> None:
        now = datetime.now(UTC)
        settings = await self.db.select(
            "automation_settings",
            {
                "select": "user_id",
                "enabled": "eq.true",
            },
        )
        sessions = (
            ("asia_session", ZoneInfo("Asia/Tokyo"), 9, 0),
            ("london_session", ZoneInfo("Europe/London"), 8, 0),
            ("new_york_session", ZoneInfo("America/New_York"), 9, 30),
        )
        for row in settings:
            for trigger, zone, hour, minute in sessions:
                local = now.astimezone(zone)
                if local.hour != hour or local.minute != minute:
                    continue
                run_key = f"{trigger}:{local.date().isoformat()}"
                await self.db.upsert(
                    "automation_agent_runs",
                    {
                        "user_id": row["user_id"],
                        "run_key": run_key,
                        "trigger": trigger,
                        "status": "scheduled",
                        "scheduled_for": now.isoformat(),
                        "model_id": MODEL_ID,
                        "reason": f"Fixed {trigger.replace('_', ' ')} review",
                    },
                    on_conflict="user_id,run_key",
                    ignore_duplicates=True,
                )

    async def _process_due_runs(self) -> None:
        enabled_rows = await self.db.select(
            "automation_settings",
            {"select": "user_id", "enabled": "eq.true"},
        )
        enabled_users = {str(row["user_id"]) for row in enabled_rows}
        if not enabled_users:
            return
        due = await self.db.select(
            "automation_agent_runs",
            {
                "select": "id,user_id,reason",
                "status": "eq.scheduled",
                "scheduled_for": f"lte.{iso_now()}",
                "order": "scheduled_for.asc",
                "limit": "3",
            },
        )
        claimed: list[dict[str, Any]] = []
        for row in due:
            if str(row["user_id"]) not in enabled_users:
                continue
            rows = await self.db.update(
                "automation_agent_runs",
                {"status": "running", "started_at": iso_now()},
                {"id": f"eq.{row['id']}", "status": "eq.scheduled"},
            )
            if rows:
                claimed.append(row)
        if not claimed:
            return
        await asyncio.gather(*(self._execute(row) for row in claimed))

    async def _execute(self, row: dict[str, Any]) -> None:
        try:
            await execute_automation_run(
                db=self.db,
                engine=self.engine,
                user_id=str(row["user_id"]),
                run_id=str(row["id"]),
                session_id=f"scheduled-{row['id']}",
                reason=row.get("reason"),
            )
        except Exception:
            logger.exception("Scheduled automation run failed run_id=%s", row["id"])


def _pick(record: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: record[key] for key in keys if record.get(key) not in (None, "")}
