"""Research decisions committed atomically beside the local trading records."""

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from psycopg.rows import dict_row

from .errors import AppError
from .materialized_definition import validate_materialized_definition

if TYPE_CHECKING:
    from .local_runtime import LocalRuntimeStore


def instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise AppError(422, "Invalid schedule time", "schedule_time_invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AppError(422, "Schedule time lacks a timezone", "schedule_time_invalid")
    return parsed


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def new_run(value: dict[str, Any]) -> dict[str, Any]:
    now = timestamp()
    return {
        "id": str(uuid4()),
        "created_at": now,
        "updated_at": now,
        "status": "scheduled",
        "outcome": None,
        "parent_agent_run_id": None,
        "model_id": "xiaomi/mimo-v2.6-pro",
        "signals_to_inspect": [],
        **value,
    }


class LocalResearchOperations:
    def __init__(self, store: "LocalRuntimeStore") -> None:
        self.store = store
        self.pool = store.pool

    async def request(self, path: str, args: dict[str, Any]) -> Any:
        operations = {
            "runtimeAutomation:context": self.context,
            "runtimeAutomation:saveSnapshot": self.save_snapshot,
            "runtimeAutomation:schedule": self.schedule,
            "runtimeAutomation:recheck": self.recheck,
            "runtimeAutomation:followup": self.followup,
            "sharedAnalysis:publish": self.publish_shared,
        }
        operation = operations.get(path)
        if operation is None:
            raise AppError(500, f"Unknown local research operation: {path}", "local_operation_unknown")
        return await operation(args)

    async def _run(self, cursor: Any, run_id: str, *, lock: bool = False) -> dict[str, Any]:
        suffix = " for update" if lock else ""
        await cursor.execute(f"select owner_id,status,data from trade.analysis_jobs where id=%s{suffix}", (run_id,))
        record = await cursor.fetchone()
        if not record:
            raise AppError(404, "Agent run unavailable", "agent_run_unavailable")
        return record

    async def context(self, args: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            run = await self._run(cursor, args["runId"])
            if run["owner_id"] != args["userId"]:
                raise AppError(404, "Agent run unavailable", "agent_run_unavailable")
            if args["userId"] == "global":
                await cursor.execute("select analysis from trade.system_settings where key='main'")
                config = await cursor.fetchone()
                settings = {"user_id": "global", **config["analysis"]} if config else None
            else:
                await cursor.execute("select automation from trade.users where user_id=%s", (args["userId"],))
                config = await cursor.fetchone()
                settings = {"user_id": args["userId"], **config["automation"]} if config else None
            parent = None
            if run["data"].get("parent_agent_run_id"):
                await cursor.execute(
                    "select owner_id,data from trade.analysis_jobs where id=%s",
                    (run["data"]["parent_agent_run_id"],),
                )
                parent_row = await cursor.fetchone()
                if parent_row and parent_row["owner_id"] == args["userId"]:
                    parent = parent_row["data"]
            await cursor.execute(
                """select count(*) from trade.strategy_capital_slots
                   where owner_id=%s and status in ('reserved','active')""",
                (args["userId"],),
            )
            occupied = (await cursor.fetchone())["count"]
            return {"run": run["data"], "settings": settings, "snapshot": None, "parent": parent, "occupied": occupied}

    async def save_snapshot(self, args: dict[str, Any]) -> str:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            run = await self._run(cursor, args["runId"], lock=True)
            if run["owner_id"] != args["userId"] or run["status"] != "running":
                raise AppError(409, "Run is not active", "run_not_active")
            await self.store._save(
                connection,
                "automation_agent_runs",
                {
                    **run["data"],
                    "market_snapshot_id": args["snapshotId"],
                },
                existing=True,
            )
        return args["snapshotId"]

    @staticmethod
    def _materialized(args: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        try:
            proposed = json.loads(args["definitionJson"])
        except (ValueError, TypeError) as error:
            raise AppError(422, "Invalid materialized strategy", "definition_changed") from error
        if not isinstance(proposed, dict):
            raise AppError(422, "Invalid materialized strategy", "definition_changed")
        validate_materialized_definition(source, proposed)
        entry = proposed.get("entry")
        if not isinstance(entry, dict) or instant(entry.get("entryAt")) != instant(args["activation"]):
            raise AppError(422, "Definition schedule mismatch", "definition_changed")
        if instant(entry.get("exitAt")) != instant(args["exit"]):
            raise AppError(422, "Definition exit mismatch", "definition_changed")
        return proposed

    async def _saved(self, cursor: Any, saved_id: str, version: int, user_id: str) -> dict[str, Any]:
        await cursor.execute("select * from trade.saved_strategies where id=%s", (saved_id,))
        saved = await cursor.fetchone()
        if (
            not saved
            or saved["deleted"]
            or not saved["enabled_for_ai"]
            or saved["version"] != version
            or (saved["user_id"] is not None and saved["user_id"] != user_id)
        ):
            raise AppError(409, "Saved strategy version unavailable", "saved_strategy_changed")
        return saved

    async def publish_shared(self, args: dict[str, Any]) -> dict[str, Any]:
        candidates = args["candidates"]
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise AppError(422, "One ranked candidate is required", "shared_candidate_invalid")
        activation, expiry, exit_at = (instant(args[key]) for key in ("activation", "expiry", "exit"))
        confidence = float(args["confidence"])
        if (
            activation <= datetime.now(UTC) + timedelta(minutes=7)
            or expiry <= activation
            or exit_at <= activation
            or not 0 <= confidence <= 1
            or not str(args["reasoning"]).strip()
        ):
            raise AppError(422, "Invalid shared decision window", "shared_window_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            run = await self._run(cursor, args["runId"], lock=True)
            if run["owner_id"] != "global" or run["status"] != "running" or run["data"].get("outcome"):
                raise AppError(409, "Shared run cannot choose another action", "shared_run_closed")
            if run["data"].get("market_snapshot_id") != args["snapshotId"]:
                raise AppError(409, "Snapshot ownership mismatch", "snapshot_mismatch")
            candidate = candidates[0]
            saved = await self._saved(cursor, candidate["id"], candidate["version"], "global")
            if saved["user_id"] is not None:
                raise AppError(409, "Shared candidate must be built-in", "shared_candidate_invalid")
            definition = self._materialized(args, saved["definition_json"])
            now = timestamp()
            decision_id, recheck_id = str(uuid4()), str(uuid4())
            proposal = {
                "id": decision_id,
                "user_id": "global",
                "agent_run_id": args["runId"],
                "strategy_id": None,
                "saved_strategy_id": candidate["id"],
                "saved_strategy_version": candidate["version"],
                "status": "scheduled",
                "name": saved["name"],
                "candidates": [
                    {
                        "id": candidate["id"],
                        "version": candidate["version"],
                        "name": saved["name"],
                        "definition_json": saved["definition_json"],
                    }
                ],
                "definition_json": definition,
                "exit_at": args["exit"],
                "activation_time": args["activation"],
                "proposal_expiry": args["expiry"],
                "ai_confidence": confidence,
                "reasoning_summary": args["reasoning"],
                "supporting_signals": args["supporting"],
                "invalidation_signals": args["invalidation"],
                "market_snapshot_id": args["snapshotId"],
                "shared_recheck_run_id": recheck_id,
                "created_at": now,
            }
            recheck = new_run(
                {
                    "id": recheck_id,
                    "user_id": "global",
                    "trigger": "activation_recheck",
                    "run_key": f"shared-recheck:{decision_id}",
                    "scheduled_for": (activation - timedelta(minutes=7)).isoformat(),
                    "strategy_proposal_id": decision_id,
                    "reason": "Recheck the shared ranked decision before account-specific execution",
                }
            )
            await self.store._save(connection, "strategy_proposals", proposal, existing=False)
            await self.store._save(connection, "automation_agent_runs", recheck, existing=False)
            await self.store._save(
                connection,
                "automation_agent_runs",
                {
                    **run["data"],
                    "outcome": "strategy_selected",
                    "shared_decision_id": decision_id,
                },
                existing=True,
            )
            return {
                "outcome": "strategy_selected",
                "proposalId": decision_id,
                "activationRecheckRunId": recheck_id,
                "activationTime": args["activation"],
                "strategy": "Shared ranked strategy decision",
                "candidateCount": 1,
                "execution": "shared_analysis",
            }

    async def schedule(self, args: dict[str, Any]) -> dict[str, Any]:
        user_id = args["userId"]
        activation, expiry, exit_at, recheck_at = (
            instant(args[key]) for key in ("activation", "expiry", "exit", "recheck")
        )
        confidence = float(args["confidence"])
        if (
            recheck_at <= datetime.now(UTC)
            or activation - recheck_at != timedelta(minutes=7)
            or exit_at <= activation
            or expiry <= activation
            or not 0 <= confidence <= 1
        ):
            raise AppError(422, "Invalid strategy schedule", "strategy_schedule_invalid")
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            run = await self._run(cursor, args["runId"], lock=True)
            if run["owner_id"] != user_id or run["status"] != "running" or run["data"].get("outcome"):
                raise AppError(409, "Run cannot select another action", "run_action_closed")
            if run["data"].get("market_snapshot_id") != args["snapshotId"]:
                raise AppError(409, "Snapshot ownership mismatch", "snapshot_mismatch")
            await cursor.execute("select automation,capital,connection from trade.users where user_id=%s", (user_id,))
            user = await cursor.fetchone()
            if not user or not user["automation"].get("enabled"):
                raise AppError(409, "Account automation disabled", "automation_disabled")
            if not user["connection"] or user["connection"].get("status") != "connected":
                raise AppError(409, "Delta connection required", "delta_not_connected")
            saved = await self._saved(cursor, args["savedId"], args["savedVersion"], user_id)
            definition = self._materialized(args, saved["definition_json"])
            maximum = {
                "full_balance": 1,
                "half_balance": 2,
                "one_third_balance": 3,
                "one_quarter_balance": 4,
            }.get(user["capital"].get("allocation_mode"))
            if maximum is not None:
                await cursor.execute(
                    """select count(*) from trade.strategy_capital_slots
                       where owner_id=%s and status in ('reserved','active')""",
                    (user_id,),
                )
                if (await cursor.fetchone())["count"] >= maximum:
                    raise AppError(409, "No capital allocation available", "capital_slots_full")
            now = timestamp()
            strategy_id, proposal_id, recheck_id = (str(uuid4()) for _ in range(3))
            strategy = {
                "id": strategy_id,
                "user_id": user_id,
                "saved_strategy_id": args["savedId"],
                "name": saved["name"],
                "status": "scheduled",
                "definition_json": definition,
                "entry_at": args["activation"],
                "exit_at": args["exit"],
                "created_at": now,
                "updated_at": now,
            }
            proposal = {
                "id": proposal_id,
                "user_id": user_id,
                "agent_run_id": args["runId"],
                "strategy_id": strategy_id,
                "saved_strategy_id": args["savedId"],
                "saved_strategy_version": args["savedVersion"],
                "status": "scheduled",
                "activation_time": args["activation"],
                "proposal_expiry": args["expiry"],
                "ai_confidence": confidence,
                "reasoning_summary": args["reasoning"],
                "supporting_signals": args["supporting"],
                "invalidation_signals": args["invalidation"],
                "market_snapshot_id": args["snapshotId"],
                "news_analysis_id": args["newsId"],
                "created_at": now,
            }
            recheck = new_run(
                {
                    "id": recheck_id,
                    "user_id": user_id,
                    "run_key": f"activation-recheck:{proposal_id}",
                    "trigger": "activation_recheck",
                    "scheduled_for": args["recheck"],
                    "reason": f"Recheck {saved['name']} before activation",
                    "strategy_proposal_id": proposal_id,
                }
            )
            await self.store._save(connection, "strategies", strategy, existing=False)
            await self.store._save(connection, "strategy_proposals", proposal, existing=False)
            await self.store._save(connection, "automation_agent_runs", recheck, existing=False)
            await self.store._save(
                connection,
                "automation_agent_runs",
                {
                    **run["data"],
                    "outcome": "strategy_selected",
                },
                existing=True,
            )
            return {
                "outcome": "strategy_selected",
                "proposalId": proposal_id,
                "strategy": saved["name"],
                "strategyVersion": saved["version"],
                "activationTime": args["activation"],
                "proposalExpiry": args["expiry"],
                "scheduledStrategyId": strategy_id,
                "activationRecheckRunId": recheck_id,
                "activationRecheckTime": args["recheck"],
                "exitTime": args["exit"],
                "execution": "live_strategy_scheduler",
            }

    async def recheck(self, args: dict[str, Any]) -> dict[str, Any]:
        user_id, proposal_id = args["userId"], args["proposalId"]
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            run = await self._run(cursor, args["runId"], lock=True)
            await cursor.execute(
                "select owner_id,status,data from trade.strategy_proposals where id=%s for update", (proposal_id,)
            )
            proposal = await cursor.fetchone()
            if (
                run["owner_id"] != user_id
                or run["status"] != "running"
                or not proposal
                or proposal["owner_id"] != user_id
            ):
                raise AppError(409, "Recheck unavailable", "recheck_unavailable")
            state, proposed = run["data"], proposal["data"]
            if state.get("trigger") != "activation_recheck" or state.get("strategy_proposal_id") != proposal_id:
                raise AppError(409, "Recheck assignment mismatch", "recheck_assignment_mismatch")
            if state.get("outcome"):
                return {"outcome": state["outcome"]}
            strategy = None
            if proposed.get("strategy_id"):
                await cursor.execute(
                    "select status,data from trade.strategies where id=%s for update", (proposed["strategy_id"],)
                )
                strategy = await cursor.fetchone()
            shared = user_id == "global"
            valid = proposal["status"] == "scheduled" and (shared or (strategy and strategy["status"] == "scheduled"))
            name = proposed.get("name") if shared else (strategy["data"].get("name") if strategy else "")
            if args["drop"] and (
                not valid
                or str(name).lower() != str(args.get("name") or "").strip().lower()
                or instant(proposed["activation_time"]) != instant(args.get("activation"))
                or not str(args.get("reason") or "").strip()
            ):
                raise AppError(409, "Drop does not match assigned strategy", "recheck_drop_mismatch")
            outcome = "strategy_dropped" if args["drop"] or not valid else "strategy_reconfirmed"
            if args["drop"]:
                message = f"Dropped by activation recheck: {args['reason']}"
                if strategy:
                    await self.store._save(
                        connection,
                        "strategies",
                        {
                            **strategy["data"],
                            "status": "cancelled",
                            "last_error": message,
                        },
                        existing=True,
                    )
                await self.store._save(
                    connection,
                    "strategy_proposals",
                    {
                        **proposed,
                        "status": "cancelled",
                        "rejection_reason": message,
                    },
                    existing=True,
                )
            await self.store._save(
                connection,
                "automation_agent_runs",
                {
                    **state,
                    "outcome": outcome,
                },
                existing=True,
            )
            return {
                "outcome": outcome,
                "strategy": strategy["data"].get("name") if strategy else None,
                "activationTime": proposed["activation_time"],
                "reason": args.get("reason"),
            }

    async def followup(self, args: dict[str, Any]) -> dict[str, Any]:
        user_id = args["userId"]
        next_at, fixed_at, previous_at, day_start, day_end = (
            instant(args[key]) for key in ("next", "fixed", "previous", "dayStart", "dayEnd")
        )
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            run = await self._run(cursor, args["runId"], lock=True)
            if run["owner_id"] != user_id or run["status"] != "running" or run["data"].get("outcome"):
                raise AppError(409, "Run cannot choose another action", "run_action_closed")
            if user_id == "global":
                await cursor.execute("select analysis from trade.system_settings where key='main'")
                result = await cursor.fetchone()
                settings = result["analysis"] if result else None
            else:
                await cursor.execute("select automation from trade.users where user_id=%s", (user_id,))
                result = await cursor.fetchone()
                settings = result["automation"] if result else None
            if not settings or not settings.get("enabled") or run["data"].get("trigger") == "agent_follow_up":
                raise AppError(409, "Follow-up chaining is disabled", "followup_unavailable")
            if not str(args["reason"]).strip() or next_at < datetime.now(UTC) + timedelta(
                minutes=int(settings.get("minimum_follow_up_minutes", 5))
            ):
                raise AppError(422, "Follow-up is too soon", "followup_too_soon")
            await cursor.execute(
                """select status,data from trade.analysis_jobs
                   where owner_id=%s and scheduled_for between %s and %s
                   order by scheduled_for limit 1001 for update""",
                (user_id, min(previous_at, day_start), max(fixed_at, next_at)),
            )
            records = await cursor.fetchall()
            if len(records) > 1000:
                raise AppError(503, "Review window too large", "followup_window_full")
            pending = sorted(
                (
                    record["data"]
                    for record in records
                    if record["status"] == "scheduled"
                    and record["data"].get("trigger") != "activation_recheck"
                    and instant(record["data"]["scheduled_for"]) > datetime.now(UTC)
                ),
                key=lambda row: instant(row["scheduled_for"]),
            )
            target = next((row for row in pending if instant(row["scheduled_for"]) <= next_at), None)
            if target is None:
                if next_at >= fixed_at:
                    raise AppError(422, "Follow-up must precede next fixed review", "followup_window_invalid")
                target = next((row for row in pending if row.get("trigger") == "agent_follow_up"), None)
                used = [
                    record["data"]
                    for record in records
                    if record["status"] != "cancelled" and record["data"].get("trigger") == "agent_follow_up"
                ]
                if not target and any(previous_at < instant(row["scheduled_for"]) < fixed_at for row in used):
                    raise AppError(409, "This review window already used its follow-up", "followup_limit")
                if not target and sum(day_start <= instant(row["scheduled_for"]) < day_end for row in used) >= int(
                    settings.get("maximum_agent_runs_per_day", 3)
                ):
                    raise AppError(409, "Daily follow-up limit reached", "followup_limit")
                followup = new_run(
                    {
                        "user_id": user_id,
                        "trigger": "agent_follow_up",
                        "run_key": f"follow-up:{next_at.strftime('%Y-%m-%dT%H:%M')}Z",
                        "scheduled_for": args["next"],
                        "reason": args["reason"],
                        "signals_to_inspect": args["signals"],
                        "market_snapshot_id": args["snapshotId"],
                        "news_analysis_id": args["newsId"],
                        "parent_agent_run_id": args["runId"],
                    }
                )
                await self.store._save(connection, "automation_agent_runs", followup, existing=False)
                await self.store._save(
                    connection,
                    "automation_agent_runs",
                    {
                        **run["data"],
                        "outcome": "wait_and_run_again",
                    },
                    existing=True,
                )
                return {"outcome": "wait_and_run_again", "scheduledRunId": followup["id"], "nextRunTime": args["next"]}
            if not target.get("parent_agent_run_id"):
                await self.store._save(
                    connection,
                    "automation_agent_runs",
                    {
                        **target,
                        "parent_agent_run_id": args["runId"],
                    },
                    existing=True,
                )
            await self.store._save(
                connection,
                "automation_agent_runs",
                {
                    **run["data"],
                    "outcome": "wait_and_run_again",
                },
                existing=True,
            )
            return {
                "outcome": "wait_and_run_again",
                "scheduledRunId": target["id"],
                "nextRunTime": target["scheduled_for"],
                "trigger": target["trigger"],
                "reusedExistingRun": True,
            }
