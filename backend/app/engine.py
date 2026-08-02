import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from .auth import credentials_for_user
from .config import Settings
from .delta import DeltaClient
from .errors import AppError
from .models import StrategyDefinition
from .strategy import deferred_control_warnings, delta_expiry, resolve_leg
from .supabase import SupabaseAdmin

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


class TradingEngine:
    def __init__(self, db: SupabaseAdmin, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def client_for_user(self, user_id: str) -> DeltaClient:
        credentials = await credentials_for_user(self.db, user_id)
        return DeltaClient(self.settings, credentials["api_key"], credentials["api_secret"])

    async def resolve_strategy(self, client: DeltaClient, definition: StrategyDefinition) -> list[dict[str, Any]]:
        chains: dict[str, list[dict[str, Any]]] = {}
        resolved: list[dict[str, Any]] = []
        for leg in definition.legs:
            expiry = leg.expiry.isoformat()
            if expiry not in chains:
                chains[expiry] = (
                    await client.option_chain(definition.instrument.underlying, delta_expiry(leg.expiry))
                )["result"]
            resolved.append(resolve_leg(leg, chains[expiry]))
        return resolved

    async def preview_strategy(self, client: DeltaClient, definition: StrategyDefinition) -> dict[str, Any]:
        legs = await self.resolve_strategy(client, definition)
        deferred = deferred_control_warnings(definition)
        warnings = [
            "Delta Exchange cannot atomically batch different option contracts. "
            "Legs execute sequentially and stop after the first failure.",
            "Market orders may fill at prices different from the preview, especially in thin option books.",
        ]
        if deferred:
            warnings.append(
                f"{', '.join(deferred)} settings are saved for review but are not automatically monitored "
                "by this scheduler version."
            )
        return {"definition": definition.model_dump(mode="json", exclude_none=True), "legs": legs, "warnings": warnings}

    async def save_strategy(self, user_id: str, definition: StrategyDefinition, status: str) -> dict[str, Any]:
        rows = await self.db.insert(
            "strategies",
            {
                "user_id": user_id,
                "name": definition.name,
                "status": status,
                "definition_json": definition.model_dump(mode="json", exclude_none=True),
                "entry_at": definition.entry.entryAt.isoformat(),
                "exit_at": definition.entry.exitAt.isoformat(),
            },
        )
        if not rows:
            raise AppError(500, "Could not save the strategy", "strategy_save_failed")
        return {"id": rows[0]["id"], "status": rows[0]["status"]}

    async def strategy_by_id(self, strategy_id: str) -> dict[str, Any]:
        rows = await self.db.select("strategies", {"select": "*", "id": f"eq.{strategy_id}", "limit": "1"})
        if not rows:
            raise AppError(404, "Strategy not found", "strategy_not_found")
        return rows[0]

    async def claim_strategy(
        self, strategy_id: str, statuses: list[str], next_status: str, execution_field: str
    ) -> None:
        rows = await self.db.update(
            "strategies",
            {"status": next_status},
            {
                "select": "id",
                "id": f"eq.{strategy_id}",
                execution_field: "is.null",
                "status": f"in.({','.join(statuses)})",
            },
        )
        if not rows:
            raise AppError(409, "Strategy is already running or cannot be executed", "execution_in_progress")

    async def record_order(self, order: dict[str, Any]) -> None:
        if not await self.db.insert("execution_orders", order):
            raise AppError(500, "Could not record an order result", "order_record_failed")

    async def execute_entry(self, strategy_id: str) -> dict[str, Any]:
        row = await self.strategy_by_id(strategy_id)
        if row.get("entry_execution_at"):
            raise AppError(409, "Strategy entry has already run", "already_executed")
        definition = StrategyDefinition.model_validate(row["definition_json"])
        client = await self.client_for_user(str(row["user_id"]))
        try:
            resolved = await self.resolve_strategy(client, definition)
            await self.claim_strategy(strategy_id, ["draft", "scheduled"], "executing_entry", "entry_execution_at")
            executions = await self.db.insert(
                "executions", {"strategy_id": strategy_id, "kind": "entry", "status": "running"}
            )
            if not executions:
                raise AppError(500, "Could not start the execution record", "execution_record_failed")
            execution_id = str(executions[0]["id"])
            failure: Exception | None = None
            for index, leg in enumerate(resolved):
                client_order_id = f"ds_{strategy_id[:8]}_{index}_{base36(int(time.time() * 1000))}"[:32]
                try:
                    mark = float(leg.get("markPrice") or 0)
                    direction = 1 if leg["position"] == "buy" else -1
                    bracket: dict[str, Any] = {}
                    if mark > 0:
                        if leg.get("targetProfit"):
                            bracket["bracket_take_profit_price"] = str(
                                max(0.00000001, mark + direction * float(leg["targetProfit"]))
                            )
                        if leg.get("stopLoss"):
                            bracket["bracket_stop_loss_price"] = str(
                                max(0.00000001, mark - direction * float(leg["stopLoss"]))
                            )
                        if leg.get("trailStop"):
                            bracket["bracket_trail_amount"] = str(leg["trailStop"])
                        if leg.get("targetProfit") or leg.get("stopLoss"):
                            bracket["bracket_stop_trigger_method"] = "mark_price"
                    payload = {
                        "product_id": leg["productId"],
                        "product_symbol": leg["productSymbol"],
                        "size": leg["lots"],
                        "side": leg["position"],
                        "order_type": leg["orderType"],
                        "time_in_force": "gtc",
                        "reduce_only": False,
                        "client_order_id": client_order_id,
                        **bracket,
                    }
                    if leg["orderType"] == "limit_order":
                        payload["limit_price"] = leg["limitPrice"]
                    order = await client.place_order(payload)
                    result = order["result"]
                    await self.record_order(
                        {
                            "execution_id": execution_id,
                            "leg_id": leg["id"],
                            "delta_order_id": str(result.get("id") or ""),
                            "client_order_id": client_order_id,
                            "product_id": leg["productId"],
                            "product_symbol": leg["productSymbol"],
                            "side": leg["position"],
                            "size": leg["lots"],
                            "state": str(result.get("state") or "submitted"),
                            "response_json": order,
                        }
                    )
                except Exception as exc:
                    failure = exc
                    await self.record_order(
                        {
                            "execution_id": execution_id,
                            "leg_id": leg["id"],
                            "client_order_id": client_order_id,
                            "product_id": leg["productId"],
                            "product_symbol": leg["productSymbol"],
                            "side": leg["position"],
                            "size": leg["lots"],
                            "state": "failed",
                            "response_json": {"error": str(exc)},
                        }
                    )
                    break
            completed = iso_now()
            await asyncio.gather(
                self.db.update(
                    "executions",
                    {
                        "status": "partial_or_failed" if failure else "completed",
                        "error": str(failure) if failure else None,
                        "completed_at": completed,
                    },
                    {"id": f"eq.{execution_id}"},
                ),
                self.db.update(
                    "strategies",
                    {
                        "status": "attention" if failure else "active",
                        "entry_execution_at": completed,
                        "last_error": str(failure) if failure else None,
                    },
                    {"id": f"eq.{strategy_id}"},
                ),
            )
            if failure:
                raise failure
            return {"executionId": execution_id, "legs": len(resolved)}
        finally:
            await client.close()

    async def execute_exit(self, strategy_id: str) -> dict[str, Any]:
        row = await self.strategy_by_id(strategy_id)
        if row.get("exit_execution_at"):
            raise AppError(409, "Strategy exit has already run", "already_exited")
        client = await self.client_for_user(str(row["user_id"]))
        try:
            entry_executions = await self.db.select(
                "executions",
                {"select": "id", "strategy_id": f"eq.{strategy_id}", "kind": "eq.entry"},
            )
            execution_ids = [str(item["id"]) for item in entry_executions]
            recorded_orders = (
                await self.db.select(
                    "execution_orders",
                    {
                        "select": "product_id,product_symbol,state",
                        "execution_id": f"in.({','.join(execution_ids)})",
                        "state": "neq.failed",
                    },
                )
                if execution_ids
                else []
            )
            products = {
                int(item["product_id"]): {
                    "product_id": int(item["product_id"]),
                    "product_symbol": item["product_symbol"],
                }
                for item in recorded_orders
            }
            positions = (await client.positions())["result"]
            await self.claim_strategy(strategy_id, ["active"], "executing_exit", "exit_execution_at")
            executions = await self.db.insert(
                "executions", {"strategy_id": strategy_id, "kind": "exit", "status": "running"}
            )
            if not executions:
                raise AppError(500, "Could not start the execution record", "execution_record_failed")
            execution_id = str(executions[0]["id"])
            failure: Exception | None = None
            for product_id, product in products.items():
                position = next((item for item in positions if int(item.get("product_id") or 0) == product_id), None)
                size = float(position.get("size") or 0) if position else 0
                if size == 0:
                    continue
                client_order_id = f"dx_{strategy_id[:8]}_{base36(int(time.time() * 1000))}"[:32]
                side = "sell" if size > 0 else "buy"
                try:
                    order = await client.place_order(
                        {
                            "product_id": product_id,
                            "product_symbol": product["product_symbol"],
                            "size": abs(size),
                            "side": side,
                            "order_type": "market_order",
                            "reduce_only": True,
                            "client_order_id": client_order_id,
                        }
                    )
                    result = order["result"]
                    await self.record_order(
                        {
                            "execution_id": execution_id,
                            "leg_id": "exit",
                            "delta_order_id": str(result.get("id") or ""),
                            "client_order_id": client_order_id,
                            "product_id": product_id,
                            "product_symbol": product["product_symbol"],
                            "side": side,
                            "size": abs(size),
                            "state": str(result.get("state") or "submitted"),
                            "response_json": order,
                        }
                    )
                except Exception as exc:
                    failure = exc
                    break
            completed = iso_now()
            await asyncio.gather(
                self.db.update(
                    "executions",
                    {
                        "status": "partial_or_failed" if failure else "completed",
                        "error": str(failure) if failure else None,
                        "completed_at": completed,
                    },
                    {"id": f"eq.{execution_id}"},
                ),
                self.db.update(
                    "strategies",
                    {
                        "status": "attention" if failure else "completed",
                        "exit_execution_at": completed,
                        "last_error": str(failure) if failure else None,
                    },
                    {"id": f"eq.{strategy_id}"},
                ),
            )
            if failure:
                raise failure
            return {"executionId": execution_id}
        finally:
            await client.close()

    async def process_due_strategies(self) -> None:
        now = utc_now()
        now_iso = now.isoformat().replace("+00:00", "Z")
        due_entries, due_exits = await asyncio.gather(
            self.db.select(
                "strategies",
                {
                    "select": "id,entry_at",
                    "status": "eq.scheduled",
                    "entry_execution_at": "is.null",
                    "entry_at": f"lte.{now_iso}",
                    "limit": "25",
                },
            ),
            self.db.select(
                "strategies",
                {
                    "select": "id",
                    "status": "eq.active",
                    "exit_execution_at": "is.null",
                    "exit_at": f"lte.{now_iso}",
                    "limit": "25",
                },
            ),
        )
        for row in due_entries:
            entry_at = datetime.fromisoformat(str(row["entry_at"]).replace("Z", "+00:00"))
            lateness = (now - entry_at).total_seconds()
            if lateness > self.settings.max_entry_lateness_seconds:
                await self.db.update(
                    "strategies",
                    {
                        "status": "attention",
                        "last_error": f"Entry skipped because the scheduler was {int(lateness)} seconds late",
                    },
                    {"id": f"eq.{row['id']}", "status": "eq.scheduled", "entry_execution_at": "is.null"},
                )
                continue
            try:
                await self.execute_entry(str(row["id"]))
            except Exception:
                logger.exception("Scheduled entry failed for strategy %s", row["id"])
        for row in due_exits:
            try:
                await self.execute_exit(str(row["id"]))
            except Exception:
                logger.exception("Scheduled exit failed for strategy %s", row["id"])


class Scheduler:
    def __init__(self, engine: TradingEngine, poll_seconds: float, enabled: bool = True) -> None:
        self.engine = engine
        self.poll_seconds = poll_seconds
        self.enabled = enabled
        self.last_started_at: str | None = None
        self.last_completed_at: str | None = None
        self.last_error: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()

    def start(self) -> None:
        if not self.enabled:
            logger.warning("Scheduler is disabled")
            return
        self.task = asyncio.create_task(self.run(), name="delta-strategy-scheduler")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except TimeoutError:
                self.task.cancel()

    async def run(self) -> None:
        logger.info("Python scheduler started; polling every %.1f seconds", self.poll_seconds)
        while not self.stop_event.is_set():
            self.last_started_at = iso_now()
            try:
                await self.engine.process_due_strategies()
                self.last_completed_at = iso_now()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Scheduler polling cycle failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.poll_seconds)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": bool(self.task and not self.task.done()),
            "pollSeconds": self.poll_seconds,
            "lastStartedAt": self.last_started_at,
            "lastCompletedAt": self.last_completed_at,
            "lastError": self.last_error,
        }
