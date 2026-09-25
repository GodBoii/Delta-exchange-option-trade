"""Atomic shared-analysis and activation checks on local PostgreSQL."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from psycopg.rows import dict_row

from .errors import AppError
from .local_research_operations import LocalResearchOperations

if TYPE_CHECKING:
    from .local_runtime import LocalRuntimeStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise AppError(422, "Invalid schedule time", "schedule_time_invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AppError(422, "Schedule time lacks a timezone", "schedule_time_invalid")
    return parsed


class LocalControl:
    def __init__(self, store: "LocalRuntimeStore") -> None:
        self.store = store
        self.pool = store.pool
        self.research = LocalResearchOperations(store)

    async def request(self, path: str, args: dict[str, Any]) -> Any:
        operations = {
            "runtimeControl:recheckStates": self.recheck_states,
            "sharedAnalysis:manual": self.manual,
            "sharedAnalysis:pendingAllocationPage": self.pending_allocation_page,
            "sharedAnalysis:allocate": self.allocate,
            "sharedAnalysis:completeAllocation": self.complete_allocation,
        }
        operation = operations.get(path)
        if operation is None:
            return await self.research.request(path, args)
        return await operation(args)

    async def recheck_states(self, args: dict[str, Any]) -> dict[str, str]:
        strategy_ids = args["strategyIds"]
        if len(strategy_ids) > 100:
            raise AppError(422, "Recheck batch exceeds 100 strategies", "recheck_batch_invalid")
        states = dict.fromkeys(strategy_ids, "ready")
        if not strategy_ids:
            return states
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select data from trade.strategy_proposals where relation_id=any(%s)", (strategy_ids,))
            by_decision: dict[str, list[str]] = {}
            for record in await cursor.fetchall():
                row = record["data"]
                decision_id = str(row.get("shared_decision_id") or row["id"])
                strategy_id = row.get("strategy_id")
                if strategy_id in states:
                    by_decision.setdefault(decision_id, []).append(strategy_id)
                    states[strategy_id] = "pending"
            if not by_decision:
                return states
            await cursor.execute(
                "select data,status from trade.analysis_jobs where relation_id=any(%s)",
                (list(by_decision),),
            )
            for record in await cursor.fetchall():
                run = record["data"]
                if run.get("trigger") != "activation_recheck":
                    continue
                if record["status"] == "completed" and run.get("outcome") == "strategy_reconfirmed":
                    state = "ready"
                elif run.get("outcome") == "strategy_dropped":
                    state = "dropped"
                elif record["status"] in {"failed", "cancelled"}:
                    state = "failed"
                else:
                    state = "pending"
                for strategy_id in by_decision.get(str(run.get("strategy_proposal_id")), []):
                    states[strategy_id] = state
        return states

    async def manual(self, args: dict[str, Any]) -> dict[str, Any]:
        requester = args["requestedBy"]
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("select pg_advisory_xact_lock(hashtextextended('shared-manual', 45))")
            await cursor.execute("select owner_user_id,analysis from trade.system_settings where key='main'")
            config = await cursor.fetchone()
            if not config or config["owner_user_id"] != requester:
                raise AppError(403, "Owner access required", "owner_required")
            if not config["analysis"].get("enabled"):
                raise AppError(409, "Global analysis is paused", "analysis_paused")
            await cursor.execute(
                """select data from trade.analysis_jobs
                   where owner_id='global' and status in ('scheduled','running')
                   order by scheduled_for limit 100"""
            )
            for record in await cursor.fetchall():
                row = record["data"]
                if row.get("trigger") != "activation_recheck" and (
                    row["status"] == "running" or _time(row["scheduled_for"]) <= datetime.now(UTC)
                ):
                    return row
            now = _now()
            row = {
                "id": str(uuid4()),
                "user_id": "global",
                "trigger": "manual",
                "run_key": f"shared-manual:{uuid4()}",
                "scheduled_for": now,
                "reason": "User requested a shared market analysis",
                "status": "scheduled",
                "outcome": None,
                "model_id": "xiaomi/mimo-v2.6-pro",
                "signals_to_inspect": [],
                "parent_agent_run_id": None,
                "created_at": now,
                "updated_at": now,
            }
            await self.store._save(connection, "automation_agent_runs", row, existing=False)
            return row

    async def _ready_decisions(self, connection: Any) -> list[dict[str, Any]]:
        rows = await connection.execute(
            """select data from trade.strategy_proposals
               where owner_id='global' and status='scheduled' and activation_time>now()
                 and not (data ? 'allocation_completed_at')
               order by activation_time limit 101"""
        )
        decisions = [record[0] for record in await rows.fetchall()]
        if len(decisions) > 100:
            raise AppError(503, "Too many pending shared decisions", "allocation_window_full")
        ready = []
        for decision in decisions:
            recheck_id = decision.get("shared_recheck_run_id")
            result = await connection.execute("select status,data from trade.analysis_jobs where id=%s", (recheck_id,))
            run = await result.fetchone()
            if run and run[0] == "completed" and run[1].get("outcome") == "strategy_reconfirmed":
                ready.append(decision)
        return ready

    async def pending_allocation_page(self, args: dict[str, Any]) -> dict[str, Any]:
        cursor = args.get("cursor") or ""
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as query:
            decisions = await self._ready_decisions(connection)
            if not decisions:
                return {"items": [], "decisions": [], "continueCursor": "", "isDone": True}
            await query.execute(
                "select user_id,automation,connection from trade.users where user_id>%s order by user_id limit 101",
                (cursor,),
            )
            accounts = await query.fetchall()
            page = accounts[:100]
            items = []
            for decision in decisions:
                for account in page:
                    if not account["automation"].get("enabled") or not account["connection"]:
                        continue
                    if account["connection"].get("status") != "connected":
                        continue
                    await query.execute(
                        """select 1 from trade.strategy_proposals
                           where owner_id=%s and unique_key=%s limit 1""",
                        (account["user_id"], f"shared:{decision['id']}"),
                    )
                    if not await query.fetchone():
                        items.append({"decisionId": decision["id"], "userId": account["user_id"]})
            return {
                "items": items,
                "decisions": [decision["id"] for decision in decisions],
                "continueCursor": page[-1]["user_id"] if len(accounts) > 100 else "",
                "isDone": len(accounts) <= 100,
            }

    async def allocate(self, args: dict[str, Any]) -> dict[str, Any]:
        decision_id, user_id = args["decisionId"], args["userId"]
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s, 46))", (f"{decision_id}:{user_id}",)
            )
            await cursor.execute("select status,data from trade.strategy_proposals where id=%s", (decision_id,))
            record = await cursor.fetchone()
            if not record or record["status"] != "scheduled" or record["data"].get("user_id") != "global":
                raise AppError(409, "Shared decision unavailable", "shared_decision_unavailable")
            decision = record["data"]
            if _time(decision["activation_time"]) <= datetime.now(UTC):
                raise AppError(409, "Shared entry window elapsed", "shared_entry_elapsed")
            await cursor.execute(
                "select status,data from trade.analysis_jobs where id=%s", (decision["shared_recheck_run_id"],)
            )
            recheck = await cursor.fetchone()
            if (
                not recheck
                or recheck["status"] != "completed"
                or recheck["data"].get("outcome") != "strategy_reconfirmed"
            ):
                raise AppError(409, "Shared recheck has not confirmed entry", "shared_recheck_pending")
            await cursor.execute("select automation,connection from trade.users where user_id=%s", (user_id,))
            account = await cursor.fetchone()
            if not account or not account["automation"].get("enabled") or not account["connection"]:
                raise AppError(409, "Account automation unavailable", "account_automation_unavailable")
            if account["connection"].get("status") != "connected":
                raise AppError(409, "Delta connection required", "delta_not_connected")
            await cursor.execute("select * from trade.saved_strategies where id=%s", (decision["saved_strategy_id"],))
            saved = await cursor.fetchone()
            if not saved or saved["deleted"] or saved["user_id"] is not None or not saved["enabled_for_ai"]:
                raise AppError(409, "Built-in strategy changed", "shared_strategy_changed")
            if saved["version"] != decision["saved_strategy_version"]:
                raise AppError(409, "Built-in strategy changed", "shared_strategy_changed")
            candidates = decision.get("candidates")
            if not isinstance(candidates, list) or not any(
                isinstance(candidate, dict)
                and candidate.get("id") == str(saved["id"])
                and candidate.get("version") == saved["version"]
                for candidate in candidates
            ):
                raise AppError(409, "Strategy was not ranked in the shared decision", "shared_strategy_changed")
            await cursor.execute(
                "select data from trade.strategy_proposals where owner_id=%s and unique_key=%s",
                (user_id, f"shared:{decision_id}"),
            )
            existing = await cursor.fetchone()
            if existing:
                return {"strategyId": existing["data"]["strategy_id"], "reused": True}
            now = _now()
            strategy_id, proposal_id = str(uuid4()), str(uuid4())
            strategy = {
                "id": strategy_id,
                "user_id": user_id,
                "name": saved["name"],
                "saved_strategy_id": str(saved["id"]),
                "status": "scheduled",
                "definition_json": decision["definition_json"],
                "entry_at": decision["activation_time"],
                "exit_at": decision["exit_at"],
                "shared_decision_id": decision_id,
                "created_at": now,
                "updated_at": now,
            }
            proposal = {
                "id": proposal_id,
                "user_id": user_id,
                "agent_run_id": decision["agent_run_id"],
                "strategy_id": strategy_id,
                "shared_decision_id": decision_id,
                "shared_recheck_run_id": decision["shared_recheck_run_id"],
                "saved_strategy_id": str(saved["id"]),
                "saved_strategy_version": saved["version"],
                "status": "scheduled",
                "activation_time": decision["activation_time"],
                "proposal_expiry": decision["proposal_expiry"],
                "ai_confidence": decision["ai_confidence"],
                "reasoning_summary": decision["reasoning_summary"],
                "supporting_signals": decision["supporting_signals"],
                "invalidation_signals": decision["invalidation_signals"],
                "market_snapshot_id": decision["market_snapshot_id"],
                "created_at": now,
            }
            await self.store._save(connection, "strategies", strategy, existing=False)
            await self.store._save(connection, "strategy_proposals", proposal, existing=False)
            return {"strategyId": strategy_id, "reused": False}

    async def complete_allocation(self, args: dict[str, Any]) -> None:
        async with self.pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "select status,data from trade.strategy_proposals where id=%s for update", (args["decisionId"],)
            )
            record = await cursor.fetchone()
            if not record or record["status"] != "scheduled" or record["data"].get("user_id") != "global":
                raise AppError(409, "Shared decision unavailable", "shared_decision_unavailable")
            decision = record["data"]
            if decision.get("allocation_completed_at"):
                return
            await cursor.execute(
                "select status,data from trade.analysis_jobs where id=%s", (decision["shared_recheck_run_id"],)
            )
            recheck = await cursor.fetchone()
            if (
                not recheck
                or recheck["status"] != "completed"
                or recheck["data"].get("outcome") != "strategy_reconfirmed"
            ):
                raise AppError(409, "Shared recheck has not confirmed entry", "shared_recheck_pending")
            await self.store._save(
                connection,
                "strategy_proposals",
                {
                    **decision,
                    "allocation_completed_at": _now(),
                },
                existing=True,
            )
