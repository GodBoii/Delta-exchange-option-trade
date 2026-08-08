import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .auth import credentials_for_user
from .config import Settings
from .delta import DeltaClient
from .errors import AppError
from .models import StrategyDefinition
from .strategy import combined_premium_metrics, deferred_control_warnings, delta_expiry, resolve_leg
from .supabase import SupabaseAdmin

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


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
        self.contract_values: dict[str, Decimal] = {}

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
        if definition.riskMode == "combined_premium":
            warnings.append(
                "The combined stop arms only after every entry leg is filled, then the backend monitors actual "
                "filled credit against live mark prices and submits separate reduce-only exits."
            )
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
                        if definition.riskMode == "combined_premium":
                            if leg["position"] == "sell" and definition.emergencyStopLossPercent:
                                emergency_multiplier = 1 + float(definition.emergencyStopLossPercent) / 100
                                bracket["bracket_stop_loss_price"] = str(max(0.00000001, mark * emergency_multiplier))
                                bracket["bracket_stop_trigger_method"] = "mark_price"
                        elif leg.get("targetProfit"):
                            bracket["bracket_take_profit_price"] = str(
                                max(0.00000001, mark + direction * float(leg["targetProfit"]))
                            )
                        if definition.riskMode == "legwise" and leg.get("stopLoss"):
                            bracket["bracket_stop_loss_price"] = str(
                                max(0.00000001, mark - direction * float(leg["stopLoss"]))
                            )
                        if definition.riskMode == "legwise" and leg.get("trailStop"):
                            bracket["bracket_trail_amount"] = str(leg["trailStop"])
                        if definition.riskMode == "legwise" and (leg.get("targetProfit") or leg.get("stopLoss")):
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
                    requested_size = decimal_value(leg["lots"])
                    unfilled_size = decimal_value(result.get("unfilled_size"), str(leg["lots"]))
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
                            "filled_size": str(max(Decimal("0"), requested_size - unfilled_size)),
                            "average_fill_price": result.get("average_fill_price"),
                            "commission": str(decimal_value(result.get("paid_commission") or result.get("commission"))),
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

    async def entry_orders(self, strategy_id: str) -> list[dict[str, Any]]:
        entry_executions = await self.db.select(
            "executions",
            {"select": "id", "strategy_id": f"eq.{strategy_id}", "kind": "eq.entry"},
        )
        execution_ids = [str(item["id"]) for item in entry_executions]
        if not execution_ids:
            return []
        return await self.db.select(
            "execution_orders",
            {
                "select": (
                    "id,leg_id,delta_order_id,client_order_id,product_id,product_symbol,side,size,state,"
                    "filled_size,average_fill_price,commission,created_at"
                ),
                "execution_id": f"in.({','.join(execution_ids)})",
                "state": "neq.failed",
            },
        )

    async def reconcile_entry_fills(
        self, client: DeltaClient, orders: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not orders:
            return orders
        product_ids = sorted({int(order["product_id"]) for order in orders})
        created = [datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00")) for order in orders]
        start_time = int((min(created).timestamp() - 5) * 1_000_000)
        response = await client.fills(product_ids, start_time)
        fills = response.get("result") or []
        by_order: dict[str, list[dict[str, Any]]] = {}
        for fill in fills:
            by_order.setdefault(str(fill.get("order_id") or ""), []).append(fill)

        reconciled: list[dict[str, Any]] = []
        for order in orders:
            order_fills = by_order.get(str(order.get("delta_order_id") or ""), [])
            if order_fills:
                filled_size = sum((decimal_value(fill.get("size")) for fill in order_fills), Decimal("0"))
                notional = sum(
                    (decimal_value(fill.get("price")) * decimal_value(fill.get("size")) for fill in order_fills),
                    Decimal("0"),
                )
                average_fill_price = notional / filled_size if filled_size else Decimal("0")
                commission = sum((decimal_value(fill.get("commission")) for fill in order_fills), Decimal("0"))
                order = {
                    **order,
                    "filled_size": str(filled_size),
                    "average_fill_price": str(average_fill_price),
                    "commission": str(commission),
                }
                await self.db.update(
                    "execution_orders",
                    {
                        "filled_size": str(filled_size),
                        "average_fill_price": str(average_fill_price),
                        "commission": str(commission),
                    },
                    {"id": f"eq.{order['id']}"},
                )
            reconciled.append(order)
        return reconciled

    async def contract_value(self, client: DeltaClient, symbol: str) -> Decimal:
        if symbol not in self.contract_values:
            product = await client.product(symbol)
            self.contract_values[symbol] = decimal_value(product.get("result", {}).get("contract_value"), "1")
        return self.contract_values[symbol]

    async def monitor_combined_strategy(self, row: dict[str, Any]) -> bool:
        definition = StrategyDefinition.model_validate(row["definition_json"])
        if definition.riskMode != "combined_premium" or not definition.combinedStopLossPercent:
            return False
        client = await self.client_for_user(str(row["user_id"]))
        try:
            orders = await self.entry_orders(str(row["id"]))
            orders = await self.reconcile_entry_fills(client, orders)
            ready = bool(orders) and all(
                decimal_value(order.get("filled_size")) >= decimal_value(order.get("size")) for order in orders
            )
            risk_state: dict[str, Any] = {
                "mode": "combined_premium",
                "status": "armed" if ready else "awaiting_fills",
                "stopPercent": str(definition.combinedStopLossPercent),
                "legs": [
                    {
                        "legId": order["leg_id"],
                        "symbol": order["product_symbol"],
                        "filledSize": str(order.get("filled_size") or "0"),
                        "requestedSize": str(order["size"]),
                    }
                    for order in orders
                ],
            }
            if not ready:
                await self.db.update(
                    "strategies",
                    {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}", "status": "eq.active"},
                )
                return False

            market_data = await asyncio.gather(
                *(client.ticker(str(order["product_symbol"])) for order in orders),
                *(self.contract_value(client, str(order["product_symbol"])) for order in orders),
            )
            ticker_results = market_data[: len(orders)]
            contract_values = market_data[len(orders) :]
            if any(decimal_value(ticker.get("result", {}).get("mark_price")) <= 0 for ticker in ticker_results):
                risk_state.update({"status": "price_unavailable", "message": "A live mark price is unavailable"})
                await self.db.update(
                    "strategies",
                    {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}", "status": "eq.active"},
                )
                return False
            priced_legs: list[dict[str, Any]] = []
            for order, ticker, multiplier in zip(orders, ticker_results, contract_values, strict=True):
                priced_legs.append(
                    {
                        "side": order["side"],
                        "filled_size": order["filled_size"],
                        "entry_price": order["average_fill_price"],
                        "mark_price": ticker.get("result", {}).get("mark_price"),
                        "contract_value": multiplier,
                    }
                )
            metrics = combined_premium_metrics(
                priced_legs, decimal_value(definition.combinedStopLossPercent)
            )
            entry_credit = metrics["entry_credit"]
            close_cost = metrics["close_cost"]
            if entry_credit <= 0:
                risk_state.update({"status": "attention", "message": "Combined entry credit is not positive"})
                await self.db.update(
                    "strategies",
                    {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}", "status": "eq.active"},
                )
                return False

            trigger_cost = metrics["trigger_close_cost"]
            risk_state.update(
                {
                    "entryCredit": str(entry_credit),
                    "currentCloseCost": str(close_cost),
                    "triggerCloseCost": str(trigger_cost),
                    "loss": str(close_cost - entry_credit),
                    "progress": str(max(Decimal("0"), (close_cost - entry_credit) / entry_credit * 100)),
                    "pricedAt": iso_now(),
                }
            )
            await self.db.update(
                "strategies",
                {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                {"id": f"eq.{row['id']}", "status": "eq.active"},
            )
            if close_cost < trigger_cost:
                return False

            triggered_at = iso_now()
            risk_state["status"] = "triggered"
            claimed = await self.db.update(
                "strategies",
                {
                    "status": "executing_exit",
                    "combined_stop_triggered_at": triggered_at,
                    "risk_state": risk_state,
                },
                {
                    "select": "id",
                    "id": f"eq.{row['id']}",
                    "status": "eq.active",
                    "combined_stop_triggered_at": "is.null",
                },
            )
            return bool(claimed)
        finally:
            await client.close()

    async def process_active_risks(self) -> None:
        active = await self.db.select(
            "strategies",
            {
                "select": "id,user_id,definition_json",
                "status": "eq.active",
                "combined_stop_triggered_at": "is.null",
                "limit": "25",
            },
        )
        for row in active:
            try:
                if await self.monitor_combined_strategy(row):
                    await self.execute_exit(str(row["id"]), preclaimed=True)
            except Exception as exc:
                logger.exception("Combined risk monitor failed for strategy %s", row["id"])
                await self.db.update(
                    "strategies",
                    {"last_error": f"Combined risk monitor: {exc}", "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}"},
                )
                await self.db.update(
                    "strategies",
                    {"status": "attention"},
                    {"id": f"eq.{row['id']}", "status": "eq.executing_exit", "exit_execution_at": "is.null"},
                )

    async def execute_exit(self, strategy_id: str, preclaimed: bool = False) -> dict[str, Any]:
        row = await self.strategy_by_id(strategy_id)
        if row.get("exit_execution_at"):
            raise AppError(409, "Strategy exit has already run", "already_exited")
        client = await self.client_for_user(str(row["user_id"]))
        try:
            recorded_orders = await self.entry_orders(strategy_id)
            products: dict[int, dict[str, Any]] = {}
            for item in recorded_orders:
                product_id = int(item["product_id"])
                filled = decimal_value(item.get("filled_size"))
                owned_size = filled if filled > 0 else decimal_value(item["size"])
                signed_size = owned_size if item["side"] == "buy" else -owned_size
                product = products.setdefault(
                    product_id,
                    {
                        "product_id": product_id,
                        "product_symbol": item["product_symbol"],
                        "signed_size": Decimal("0"),
                    },
                )
                product["signed_size"] += signed_size
            if not preclaimed:
                await self.claim_strategy(strategy_id, ["active"], "executing_exit", "exit_execution_at")
            elif row.get("status") != "executing_exit":
                raise AppError(409, "Combined stop exit was not claimed", "exit_not_claimed")
            executions = await self.db.insert(
                "executions", {"strategy_id": strategy_id, "kind": "exit", "status": "running"}
            )
            if not executions:
                raise AppError(500, "Could not start the execution record", "execution_record_failed")
            execution_id = str(executions[0]["id"])
            failures: list[Exception] = []

            async def close_product(index: int, product_id: int, product: dict[str, Any]) -> None:
                try:
                    position_response = await client.position(product_id)
                    position = position_response.get("result") or {}
                    live_size = decimal_value(position.get("size"))
                    owned_signed_size = decimal_value(product["signed_size"])
                    if live_size == 0 or owned_signed_size == 0:
                        return
                    if live_size * owned_signed_size <= 0:
                        raise AppError(
                            409,
                            f"Live {product['product_symbol']} position does not match the strategy direction",
                            "position_direction_mismatch",
                        )
                    close_size = min(abs(live_size), abs(owned_signed_size))
                    side = "sell" if live_size > 0 else "buy"
                    client_order_id = (
                        f"dx_{strategy_id[:8]}_{index}_{base36(int(time.time() * 1000))}"[:32]
                    )
                    order = await client.place_order(
                        {
                            "product_id": product_id,
                            "product_symbol": product["product_symbol"],
                            "size": int(close_size),
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
                            "size": int(close_size),
                            "state": str(result.get("state") or "submitted"),
                            "response_json": order,
                        }
                    )
                except Exception as exc:
                    failures.append(exc)

            await asyncio.gather(
                *(
                    close_product(index, product_id, product)
                    for index, (product_id, product) in enumerate(products.items())
                )
            )
            failure = failures[0] if failures else None
            completed = iso_now()
            risk_state = dict(row.get("risk_state") or {})
            if row.get("combined_stop_triggered_at"):
                risk_state["status"] = "attention" if failure else "exit_submitted"
                risk_state["exitSubmittedAt"] = completed
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
                        "risk_state": risk_state,
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
        await self.process_active_risks()
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
