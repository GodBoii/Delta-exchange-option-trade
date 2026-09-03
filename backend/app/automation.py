from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from .auth import current_account, require_user
from .automation_schedule import fixed_runs_between, ist_text, next_fixed_run, utc_text
from .capital import percentage_concurrency_limit
from .engine import TradingEngine, iso_now
from .errors import AppError
from .supabase import SupabaseAdmin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/automation", tags=["automation"])
news_analyzer_url = "http://news-analyzer:8002"
MODEL_ID = "deepseek/deepseek-v4-flash-vision-exp"
FIXED_RUN_LOOKAHEAD = timedelta(hours=36)
FIXED_RUN_CATCH_UP = timedelta(minutes=5)
FIXED_RUN_SYNC_SECONDS = 60.0
MAX_AUTOMATION_RUN_LATENESS = timedelta(minutes=10)
MAX_AUTOMATION_RUN_RUNTIME = timedelta(minutes=20)
MAX_PARALLEL_AUTOMATION_RUNS = 3

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
    policy = await engine.capital_policy(user_id)
    fixed = next_fixed_run(datetime.now(UTC))
    active, upcoming_runs = await asyncio.gather(
        engine.db.select(
            "strategies",
            {
                "select": "id,name,status,saved_strategy_id,entry_at,exit_at",
                "user_id": f"eq.{user_id}",
                "status": "in.(scheduled,executing_entry,active,executing_exit,attention)",
                "limit": "25",
            },
        ),
        engine.db.select(
            "automation_agent_runs",
            {
                "select": "id,trigger,scheduled_for,reason,signals_to_inspect",
                "user_id": f"eq.{user_id}",
                "status": "eq.scheduled",
                "trigger": "neq.activation_recheck",
                "scheduled_for": f"gt.{iso_now()}",
                "order": "scheduled_for.asc",
                "limit": "5",
            },
        ),
    )
    return {
        "activeStrategies": active,
        "nextFixedAgentRun": {
            "trigger": fixed.trigger,
            "scheduledForUtc": utc_text(fixed.scheduled_for),
            "scheduledForIst": ist_text(fixed.scheduled_for),
        },
        "upcomingAgentRuns": [
            {
                **_pick(row, "id", "trigger", "reason", "signals_to_inspect"),
                "scheduledForUtc": row["scheduled_for"],
                "scheduledForIst": ist_text(datetime.fromisoformat(row["scheduled_for"].replace("Z", "+00:00"))),
            }
            for row in upcoming_runs
        ],
        "maximumConcurrentStrategies": percentage_concurrency_limit(policy.allocation_mode) or "calculated_at_entry",
    }


async def build_activation_recheck_context(
    db: SupabaseAdmin,
    user_id: str,
    proposal_id: str,
) -> dict[str, Any]:
    proposals = await db.select(
        "strategy_proposals",
        {
            "select": (
                "id,agent_run_id,strategy_id,status,activation_time,proposal_expiry,ai_confidence,"
                "reasoning_summary,supporting_signals,invalidation_signals"
            ),
            "id": f"eq.{proposal_id}",
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    )
    if not proposals:
        raise AppError(404, "The strategy proposal for this activation recheck is unavailable", "proposal_not_found")
    proposal = proposals[0]
    strategies, parent_runs = await asyncio.gather(
        db.select(
            "strategies",
            {
                "select": "id,name,status,definition_json,entry_at,exit_at",
                "id": f"eq.{proposal['strategy_id']}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        ),
        db.select(
            "automation_agent_runs",
            {
                "select": "id,scheduled_for,completed_at,report_markdown",
                "id": f"eq.{proposal['agent_run_id']}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        ),
    )
    if not strategies:
        raise AppError(404, "The scheduled strategy for this activation recheck is unavailable", "strategy_not_found")
    strategy = strategies[0]
    if proposal["status"] != "scheduled" or strategy["status"] != "scheduled":
        raise AppError(409, "The strategy is no longer awaiting activation", "strategy_not_scheduled")
    parent = parent_runs[0] if parent_runs else {}
    if not str(parent.get("report_markdown") or "").strip():
        raise AppError(409, "The selecting agent's final report is unavailable", "selection_report_missing")
    return {
        "proposalId": proposal["id"],
        "selectedStrategy": {
            "id": strategy["id"],
            "name": strategy["name"],
            "activationTime": proposal["activation_time"],
            "exitTime": strategy["exit_at"],
            "proposalExpiry": proposal["proposal_expiry"],
            "definition": strategy["definition_json"],
        },
        "originalSelection": {
            "runId": parent.get("id"),
            "runTime": parent.get("scheduled_for"),
            "completedAt": parent.get("completed_at"),
            "confidence": float(proposal["ai_confidence"]),
            "reasoning": proposal["reasoning_summary"],
            "supportingSignals": proposal.get("supporting_signals") or [],
            "invalidationSignals": proposal.get("invalidation_signals") or [],
            "finalResponse": parent.get("report_markdown"),
        },
    }


async def execute_automation_run(
    *,
    db: SupabaseAdmin,
    engine: TradingEngine,
    user_id: str,
    run_id: str,
    session_id: str,
    trigger: str,
    reason: str | None,
    signals_to_inspect: list[str] | None = None,
    strategy_proposal_id: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        if trigger == "activation_recheck":
            if not strategy_proposal_id:
                raise AppError(500, "Activation recheck is missing its strategy proposal", "recheck_proposal_missing")
            account_context = await build_activation_recheck_context(db, user_id, strategy_proposal_id)
        else:
            account_context = await build_account_context(engine, user_id)
        timeout_seconds = 120 if trigger == "activation_recheck" else 900
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=5)) as client:
            response = await client.post(
                f"{news_analyzer_url}/v1/automation/analyze",
                json={
                    "userId": user_id,
                    "agentRunId": run_id,
                    "sessionId": session_id,
                    "accountContext": account_context,
                    "trigger": trigger,
                    "triggerReason": reason,
                    "signalsToInspect": signals_to_inspect or [],
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
            {"id": f"eq.{run_id}", "user_id": f"eq.{user_id}", "status": "eq.running"},
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
        try:
            recorded = await db.select(
                "automation_agent_runs",
                {
                    "select": "outcome,market_snapshot_id",
                    "id": f"eq.{run_id}",
                    "user_id": f"eq.{user_id}",
                    "limit": "1",
                },
            )
        except Exception:
            logger.exception("Could not recover automation outcome run_id=%s", run_id)
            recorded = []
        if recorded and recorded[0].get("outcome"):
            report = (
                "## Decision\n\nThe terminal action was recorded, "
                "but the model provider did not return the final report."
            )
            payload = {
                "outcome": recorded[0]["outcome"],
                "marketSnapshotId": recorded[0].get("market_snapshot_id"),
                "report": report,
                "memberResponses": [],
                "toolCalls": [],
            }
            await db.update(
                "automation_agent_runs",
                {
                    "status": "completed",
                    "completed_at": iso_now(),
                    "report_markdown": report,
                    "error": "Final report unavailable",
                },
                {"id": f"eq.{run_id}", "user_id": f"eq.{user_id}", "status": "eq.running"},
            )
            logger.warning("Recovered committed automation outcome run_id=%s outcome=%s", run_id, payload["outcome"])
            return payload
        await db.update(
            "automation_agent_runs",
            {"status": "failed", "completed_at": iso_now(), "error": str(error)},
            {"id": f"eq.{run_id}", "user_id": f"eq.{user_id}", "status": "eq.running"},
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
    settings, capital_policy, strategies, runs, upcoming_runs, proposals = await asyncio.gather(
        ensure_settings(db, user_id),
        request.app.state.engine.capital_policy(user_id),
        db.select(
            "saved_strategies",
            {
                "select": "id,user_id,name,version,enabled_for_ai",
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
                "status": "in.(running,completed,failed)",
                "order": "started_at.desc",
                "limit": "20",
            },
        ),
        db.select(
            "automation_agent_runs",
            {
                "select": "id,trigger,scheduled_for",
                "user_id": f"eq.{user_id}",
                "status": "eq.scheduled",
                "scheduled_for": f"gt.{iso_now()}",
                "order": "scheduled_for.asc",
                "limit": "6",
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
    strategies = [
        row
        for row in strategies
        if not (row.get("user_id") is None and row["name"] in {"Iron condor", "Iron butterfly"})
    ]
    snapshot_ids = [str(row["market_snapshot_id"]) for row in runs if row.get("market_snapshot_id")]
    snapshots = (
        await db.select(
            "automation_market_snapshots",
            {
                "select": "id,chart_images:market_json->chartImages",
                "id": f"in.({','.join(snapshot_ids)})",
                "user_id": f"eq.{user_id}",
            },
        )
        if snapshot_ids
        else []
    )
    stored_charts = {
        str(snapshot["id"]): snapshot.get("chart_images") or [] for snapshot in snapshots
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
            "maximumConcurrentStrategies": percentage_concurrency_limit(capital_policy.allocation_mode),
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
        "upcomingRuns": [
            {
                "id": row["id"],
                "trigger": row["trigger"],
                "scheduledFor": row["scheduled_for"],
            }
            for row in upcoming_runs
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
            "status": "scheduled",
            "scheduled_for": iso_now(),
            "model_id": MODEL_ID,
            "reason": body.reason or "Manual automation review",
        },
    )
    if not rows:
        raise AppError(500, "Could not create the automation run", "automation_run_create_failed")
    run_id = str(rows[0]["id"])
    claimed = await db.rpc("claim_automation_agent_run", {"p_user_id": user_id, "p_run_id": run_id})
    if not claimed:
        await db.update(
            "automation_agent_runs",
            {"status": "cancelled", "completed_at": iso_now(), "error": "Another automation run is active"},
            {"id": f"eq.{run_id}", "status": "eq.scheduled"},
        )
        raise AppError(409, "Another automation run is already active", "automation_run_active")
    payload = await execute_automation_run(
        db=db,
        engine=engine,
        user_id=user_id,
        run_id=run_id,
        session_id=f"run-{run_id}",
        trigger="manual",
        reason=body.reason or "Manual automation review",
    )
    return {"success": True, **payload}


class AutomationScheduler:
    def __init__(self, db: SupabaseAdmin, engine: TradingEngine, poll_seconds: float = 30.0) -> None:
        self.db = db
        self.engine = engine
        self.poll_seconds = max(10.0, poll_seconds)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.running_tasks: set[asyncio.Task[None]] = set()
        self.last_fixed_sync = 0.0

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="automation-agent-scheduler")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.task, timeout=5)
        if self.running_tasks:
            with suppress(TimeoutError):
                await asyncio.wait_for(asyncio.gather(*self.running_tasks), timeout=5)

    async def run(self) -> None:
        logger.info("Automation scheduler started; polling every %.1f seconds", self.poll_seconds)
        while not self.stop_event.is_set():
            try:
                monotonic_now = time.monotonic()
                if monotonic_now - self.last_fixed_sync >= FIXED_RUN_SYNC_SECONDS:
                    await self._enqueue_session_reviews()
                    self.last_fixed_sync = monotonic_now
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
        fixed_runs = fixed_runs_between(now - FIXED_RUN_CATCH_UP, now + FIXED_RUN_LOOKAHEAD)
        payload = [
            {
                "user_id": row["user_id"],
                "run_key": run.run_key,
                "trigger": run.trigger,
                "status": "scheduled",
                "scheduled_for": utc_text(run.scheduled_for),
                "model_id": MODEL_ID,
                "reason": f"Fixed {run.trigger.replace('_', ' ')} review",
            }
            for row in settings
            for run in fixed_runs
        ]
        if payload:
            await self.db.rpc("ensure_automation_fixed_runs", {"p_runs": payload})
            await self.db.rpc("cancel_redundant_automation_followups", {})

    async def _process_due_runs(self) -> None:
        stale_before = utc_text(datetime.now(UTC) - MAX_AUTOMATION_RUN_RUNTIME)
        await self.db.update(
            "automation_agent_runs",
            {"status": "failed", "completed_at": iso_now(), "error": "Automation run exceeded its time limit"},
            {"status": "eq.running", "started_at": f"lt.{stale_before}"},
        )
        available = MAX_PARALLEL_AUTOMATION_RUNS - len(self.running_tasks)
        if available <= 0:
            return
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
                "select": "id,user_id,trigger,reason,signals_to_inspect,scheduled_for,strategy_proposal_id",
                "status": "eq.scheduled",
                "scheduled_for": f"lte.{iso_now()}",
                "order": "scheduled_for.asc",
                "limit": str(max(12, available * 4)),
            },
        )
        now = datetime.now(UTC)
        for row in due:
            if available <= 0:
                break
            if str(row["user_id"]) not in enabled_users:
                continue
            scheduled_for = datetime.fromisoformat(str(row["scheduled_for"]).replace("Z", "+00:00"))
            if now - scheduled_for > MAX_AUTOMATION_RUN_LATENESS:
                await self.db.update(
                    "automation_agent_runs",
                    {"status": "cancelled", "completed_at": iso_now(), "error": "Scheduled review became stale"},
                    {"id": f"eq.{row['id']}", "status": "eq.scheduled"},
                )
                continue
            claimed = await self.db.rpc(
                "claim_automation_agent_run",
                {"p_user_id": str(row["user_id"]), "p_run_id": str(row["id"])},
            )
            if not claimed:
                continue
            task = asyncio.create_task(self._execute(claimed[0]), name=f"automation-run-{row['id']}")
            self.running_tasks.add(task)
            task.add_done_callback(self.running_tasks.discard)
            available -= 1

    async def _execute(self, row: dict[str, Any]) -> None:
        try:
            await execute_automation_run(
                db=self.db,
                engine=self.engine,
                user_id=str(row["user_id"]),
                run_id=str(row["id"]),
                session_id=f"scheduled-{row['id']}",
                trigger=str(row["trigger"]),
                reason=row.get("reason"),
                signals_to_inspect=row.get("signals_to_inspect") or [],
                strategy_proposal_id=row.get("strategy_proposal_id"),
            )
        except Exception:
            logger.exception("Scheduled automation run failed run_id=%s", row["id"])


def _pick(record: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: record[key] for key in keys if record.get(key) not in (None, "")}
