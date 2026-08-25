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
from .strategy import (
    deferred_control_warnings,
    delta_expiry,
    resolve_leg,
    strategy_level_metrics,
)
from .supabase import SupabaseAdmin

logger = logging.getLogger(__name__)

# Columns added by migration 004. Execution must never fail because an audit
# field is missing, so writes degrade to the pre-migration column set instead.
OPTIONAL_ORDER_METADATA = (
    "order_type",
    "limit_price",
    "reference_price",
    "contract_value",
    "slippage",
    "slippage_percent",
)

TERMINAL_SCHEDULED_ENTRY_CODES = frozenset(
    {
        "automatic_lot_too_large",
        "automation_balance_unavailable",
        "automatic_lot_risk_invalid",
        "capital_slots_full",
        "option_chain_empty",
        "spot_price_missing",
        "strike_not_found",
        "delta_not_connected",
    }
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def slippage_fields(side: str, reference: Any, average: Any) -> dict[str, str]:
    """
    Execution slippage against the mark price observed before submission.

    The sign is normalised so positive always means adverse: a buy that filled
    above the reference, or a sell that filled below it. Without a usable
    reference or fill price there is nothing honest to record, so nothing is.
    """
    reference_price = optional_decimal(reference)
    average_price = optional_decimal(average)
    if not reference_price or not average_price or reference_price <= 0 or average_price <= 0:
        return {}
    direction = Decimal("1") if side == "buy" else Decimal("-1")
    slippage = (average_price - reference_price) * direction
    return {
        "slippage": str(slippage),
        "slippage_percent": str(slippage / reference_price * Decimal("100")),
    }


def order_cash_flow(order: dict[str, Any]) -> Decimal:
    """
    Signed premium moved by one recorded order, in quote currency.

    Selling collects premium (positive), buying pays it (negative). Contract
    value converts lots into underlying units; it defaults to 1 so pre-migration
    rows still produce a directionally correct figure.
    """
    filled = decimal_value(order.get("filled_size"))
    if filled <= 0 and str(order.get("state")) == "closed":
        filled = decimal_value(order.get("size"))
    price = optional_decimal(order.get("average_fill_price")) or Decimal("0")
    contract_value = optional_decimal(order.get("contract_value")) or Decimal("1")
    direction = Decimal("1") if order.get("side") == "sell" else Decimal("-1")
    return direction * price * filled * contract_value


def settlement_summary(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Money view of a run, rebuilt from the recorded orders rather than stored
    running totals, so it is identical whether it is computed at exit time or
    when the Information panel is opened months later.
    """
    entry_premium = Decimal("0")
    exit_premium = Decimal("0")
    commission = Decimal("0")
    slippage_cost = Decimal("0")
    requested_lots = Decimal("0")
    filled_lots = Decimal("0")
    closed_lots = Decimal("0")
    symbols: dict[str, dict[str, Decimal]] = {}

    for order in orders:
        is_exit = str(order.get("kind")) == "exit"
        cash = order_cash_flow(order)
        filled = decimal_value(order.get("filled_size"))
        if filled <= 0 and str(order.get("state")) == "closed":
            filled = decimal_value(order.get("size"))
        commission += decimal_value(order.get("commission"))
        slippage = optional_decimal(order.get("slippage"))
        contract_value = optional_decimal(order.get("contract_value")) or Decimal("1")
        if slippage is not None:
            slippage_cost += slippage * filled * contract_value
        if is_exit:
            exit_premium += cash
            closed_lots += filled
        else:
            entry_premium += cash
            requested_lots += decimal_value(order.get("size"))
            filled_lots += filled
        symbol = str(order.get("product_symbol") or "unknown")
        bucket = symbols.setdefault(
            symbol,
            {
                "entryPremium": Decimal("0"),
                "exitPremium": Decimal("0"),
                "commission": Decimal("0"),
                "entryLots": Decimal("0"),
                "exitLots": Decimal("0"),
            },
        )
        bucket["exitPremium" if is_exit else "entryPremium"] += cash
        bucket["exitLots" if is_exit else "entryLots"] += filled
        bucket["commission"] += decimal_value(order.get("commission"))

    gross = entry_premium + exit_premium
    return {
        "entryPremium": str(entry_premium),
        "exitPremium": str(exit_premium),
        "grossPnl": str(gross),
        "commission": str(commission),
        "realizedPnl": str(gross - commission),
        "slippageCost": str(slippage_cost),
        "requestedLots": str(requested_lots),
        "filledLots": str(filled_lots),
        "closedLots": str(closed_lots),
        "fullyClosed": bool(filled_lots > 0 and closed_lots >= filled_lots),
        "bySymbol": [
            {
                "symbol": symbol,
                "entryPremium": str(bucket["entryPremium"]),
                "exitPremium": str(bucket["exitPremium"]),
                "commission": str(bucket["commission"]),
                "entryLots": str(bucket["entryLots"]),
                "exitLots": str(bucket["exitLots"]),
                "realizedPnl": str(bucket["entryPremium"] + bucket["exitPremium"] - bucket["commission"]),
            }
            for symbol, bucket in sorted(symbols.items())
        ],
    }


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

    async def apply_automatic_lots(
        self,
        client: DeltaClient,
        definition: StrategyDefinition,
        resolved: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if definition.lotsMode != "auto":
            return resolved
        balances = (await client.balances()).get("result") or []
        available_values = [
            decimal_value(item.get("available_balance"))
            for item in balances
            if isinstance(item, dict) and decimal_value(item.get("available_balance")) > 0
        ]
        if not available_values:
            raise AppError(409, "No available Delta balance can fund this strategy", "automation_balance_unavailable")
        slot_value = max(available_values) / Decimal("3")
        contract_values = await asyncio.gather(
            *(self.contract_value(client, str(leg["productSymbol"])) for leg in resolved)
        )

        signed_premium = Decimal("0")
        for leg, multiplier in zip(resolved, contract_values, strict=True):
            mark = decimal_value(leg.get("markPrice"))
            if mark <= 0 or multiplier <= 0:
                raise AppError(409, "A live option price is unavailable for automatic lots", "option_price_unavailable")
            direction = Decimal("1") if leg["position"] == "sell" else Decimal("-1")
            signed_premium += direction * mark * multiplier
            leg["contractValue"] = str(multiplier)

        if definition.riskBasis == "net_debit":
            risk_per_lot = -signed_premium
        elif definition.riskBasis == "defined_max_loss":
            widths: list[Decimal] = []
            for option_type in ("call", "put"):
                short_strikes = [
                    decimal_value(leg.get("strike"))
                    for leg in resolved
                    if leg["optionType"] == option_type and leg["position"] == "sell"
                ]
                long_strikes = [
                    decimal_value(leg.get("strike"))
                    for leg in resolved
                    if leg["optionType"] == option_type and leg["position"] == "buy"
                ]
                if short_strikes and long_strikes:
                    widths.append(
                        min(abs(long - short) for long in long_strikes for short in short_strikes) * contract_values[0]
                    )
            risk_per_lot = max(widths, default=Decimal("0")) - signed_premium
        else:
            risk_per_lot = signed_premium * decimal_value(definition.stopLossPercent) / Decimal("100")
        if risk_per_lot <= 0:
            raise AppError(409, "Automatic lot risk could not be calculated", "automatic_lot_risk_invalid")

        lots_by_risk = int(slot_value // risk_per_lot)
        hard_cap = definition.maximumLots or 1
        lots = min(lots_by_risk, hard_cap)
        if lots < 1:
            raise AppError(409, "One lot does not fit inside an account capital slot", "automatic_lot_too_large")
        return [{**leg, "lots": lots} for leg in resolved]

    async def reserve_capital_slot(self, user_id: str, strategy_id: str) -> int:
        slot = await self.db.rpc(
            "reserve_strategy_capital_slot",
            {"p_user_id": user_id, "p_strategy_id": strategy_id},
        )
        if slot is None:
            raise AppError(409, "All three account capital slots are occupied", "capital_slots_full")
        return int(slot)

    async def release_capital_slot(self, user_id: str, strategy_id: str) -> None:
        await self.db.rpc(
            "release_strategy_capital_slot",
            {"p_user_id": user_id, "p_strategy_id": strategy_id},
        )

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

    async def save_strategy(
        self,
        user_id: str,
        definition: StrategyDefinition,
        status: str,
        saved_strategy_id: str | None = None,
    ) -> dict[str, Any]:
        if status == "scheduled":
            now = utc_now()
            if definition.entry.exitAt <= now:
                raise AppError(400, "The scheduled exit time is already in the past", "exit_time_passed")
            lateness = (now - definition.entry.entryAt).total_seconds()
            if lateness > self.settings.max_entry_lateness_seconds:
                raise AppError(400, "The scheduled entry time is too far in the past", "entry_time_passed")
        if saved_strategy_id:
            saved = await self.db.select(
                "saved_strategies",
                {
                    "select": "id",
                    "id": f"eq.{saved_strategy_id}",
                    "user_id": f"eq.{user_id}",
                    "limit": "1",
                },
            )
            if not saved:
                raise AppError(404, "Saved strategy not found", "saved_strategy_not_found")
        rows = await self.db.insert(
            "strategies",
            {
                "user_id": user_id,
                "saved_strategy_id": saved_strategy_id,
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

    async def cancel_strategy(self, strategy_id: str, user_id: str) -> None:
        rows = await self.db.update(
            "strategies",
            {"status": "cancelled", "last_error": None},
            {
                "select": "id",
                "id": f"eq.{strategy_id}",
                "user_id": f"eq.{user_id}",
                "status": "in.(draft,scheduled)",
            },
        )
        if not rows:
            raise AppError(409, "Only draft or scheduled strategies can be cancelled", "cannot_cancel")

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

    async def claim_exit(self, strategy_id: str) -> None:
        rows = await self.db.update(
            "strategies",
            {"status": "executing_exit", "exit_execution_at": None},
            {
                "select": "id",
                "id": f"eq.{strategy_id}",
                "status": "in.(active,attention)",
            },
        )
        if not rows:
            raise AppError(409, "Strategy is already exiting or cannot be exited", "exit_in_progress")

    async def record_order(self, order: dict[str, Any]) -> None:
        payload = {
            key: value for key, value in order.items() if value is not None or key not in OPTIONAL_ORDER_METADATA
        }
        try:
            rows = await self.db.insert("execution_orders", payload)
        except AppError:
            stripped = {key: value for key, value in payload.items() if key not in OPTIONAL_ORDER_METADATA}
            if stripped == payload:
                raise
            logger.warning(
                "Recording execution metadata failed; retrying without the migration 004 columns. "
                "Apply supabase/migrations/004_run_execution_metadata.sql to keep slippage and premium history."
            )
            rows = await self.db.insert("execution_orders", stripped)
        if not rows:
            raise AppError(500, "Could not record an order result", "order_record_failed")

    async def write_audit(self, table: str, payload: dict[str, Any], params: dict[str, str]) -> None:
        """
        Best-effort write for reporting-only columns. A failure here must never
        change the outcome of a trade, so it is logged and swallowed.
        """
        try:
            await self.db.update(table, payload, params)
        except Exception as exc:
            logger.warning("Could not persist run metadata on %s: %s", table, exc)

    async def live_position_size(self, client: DeltaClient, product_id: int) -> Decimal:
        response = await client.position(product_id)
        return decimal_value((response.get("result") or {}).get("size"))

    async def place_reduce_only_close(
        self,
        client: DeltaClient,
        product_id: int,
        size: Decimal,
        initial_size: Decimal,
        client_order_id: str,
        product_symbol: str | None = None,
    ) -> dict[str, Any]:
        if size <= 0 or size != size.to_integral_value():
            raise AppError(409, "Delta position size is not a positive whole number", "invalid_close_size")
        if initial_size == 0 or size > abs(initial_size):
            raise AppError(409, "Close size is larger than the live position", "invalid_close_size")

        payload: dict[str, Any] = {
            "product_id": product_id,
            "size": int(size),
            "side": "sell" if initial_size > 0 else "buy",
            "order_type": "market_order",
            "reduce_only": True,
            "client_order_id": client_order_id,
        }
        if product_symbol:
            payload["product_symbol"] = product_symbol
        order = await client.place_order(payload)
        result = order.get("result") or {}
        if not result.get("id"):
            raise AppError(502, "Delta accepted no identifiable close order", "invalid_close_response")
        return order

    async def verify_position_reduction(
        self,
        client: DeltaClient,
        product_id: int,
        size: Decimal,
        initial_size: Decimal,
    ) -> Decimal:
        expected_size = initial_size - (size if initial_size > 0 else -size)
        deadline = time.monotonic() + self.settings.exit_verify_timeout_seconds
        while True:
            live_size = await self.live_position_size(client, product_id)
            if live_size == 0 or (
                expected_size != 0 and live_size * initial_size > 0 and abs(live_size) <= abs(expected_size)
            ):
                return live_size
            if time.monotonic() >= deadline:
                raise AppError(
                    502,
                    f"Delta did not confirm position reduction for product {product_id}",
                    "exit_not_confirmed",
                )
            await asyncio.sleep(self.settings.exit_verify_poll_seconds)

    async def submit_verified_close(
        self,
        client: DeltaClient,
        product_id: int,
        size: Decimal,
        initial_size: Decimal,
        client_order_id: str,
        product_symbol: str | None = None,
    ) -> tuple[dict[str, Any], Decimal]:
        order = await self.place_reduce_only_close(
            client, product_id, size, initial_size, client_order_id, product_symbol
        )
        remaining_size = await self.verify_position_reduction(client, product_id, size, initial_size)
        return order, remaining_size

    async def close_account_position(self, user_id: str, product_id: int) -> dict[str, Any]:
        client = await self.client_for_user(user_id)
        try:
            open_orders = (await client.open_orders([product_id])).get("result") or []
            for order in open_orders:
                order_id = order.get("id")
                if order_id is not None:
                    await client.cancel_order(int(order_id), product_id)
            initial_size = await self.live_position_size(client, product_id)
            if initial_size == 0:
                raise AppError(409, "This Delta position is already closed", "position_already_closed")
            client_order_id = f"dp_{product_id}_{base36(int(time.time() * 1000))}"[:32]
            order, remaining_size = await self.submit_verified_close(
                client,
                product_id,
                abs(initial_size),
                initial_size,
                client_order_id,
            )
            return {
                "orderId": str((order.get("result") or {}).get("id")),
                "productId": product_id,
                "closedSize": str(abs(initial_size)),
                "remainingSize": str(remaining_size),
                "cancelledOrders": len(open_orders),
                "verified": True,
            }
        finally:
            await client.close()

    async def execute_entry(self, strategy_id: str) -> dict[str, Any]:
        row = await self.strategy_by_id(strategy_id)
        if row.get("entry_execution_at"):
            raise AppError(409, "Strategy entry has already run", "already_executed")
        definition = StrategyDefinition.model_validate(row["definition_json"])
        client = await self.client_for_user(str(row["user_id"]))
        try:
            resolved = await self.resolve_strategy(client, definition)
            resolved = await self.apply_automatic_lots(client, definition, resolved)
            capital_slot = await self.reserve_capital_slot(str(row["user_id"]), strategy_id)
            try:
                await self.claim_strategy(strategy_id, ["draft", "scheduled"], "executing_entry", "entry_execution_at")
                executions = await self.db.insert(
                    "executions", {"strategy_id": strategy_id, "kind": "entry", "status": "running"}
                )
                if not executions:
                    raise AppError(500, "Could not start the execution record", "execution_record_failed")
            except Exception:
                await self.release_capital_slot(str(row["user_id"]), strategy_id)
                raise
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
                    reference_price = str(mark) if mark > 0 else None
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
                            "order_type": leg["orderType"],
                            "limit_price": str(leg["limitPrice"]) if leg.get("limitPrice") is not None else None,
                            "reference_price": reference_price,
                            "response_json": order,
                            **slippage_fields(str(leg["position"]), reference_price, result.get("average_fill_price")),
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
                            "order_type": leg["orderType"],
                            "limit_price": str(leg["limitPrice"]) if leg.get("limitPrice") is not None else None,
                            "reference_price": str(leg.get("markPrice")) if leg.get("markPrice") else None,
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
            await self.db.update(
                "strategy_capital_slots",
                {"status": "active"},
                {
                    "user_id": f"eq.{row['user_id']}",
                    "strategy_id": f"eq.{strategy_id}",
                    "status": "eq.reserved",
                },
            )
            await self.write_audit(
                "strategy_proposals",
                {"status": "activated"},
                {"strategy_id": f"eq.{strategy_id}", "status": "eq.scheduled"},
            )
            return {"executionId": execution_id, "legs": len(resolved), "capitalSlot": capital_slot}
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
                # `*` keeps this reader working whether or not the execution
                # metadata migration has been applied to the project.
                "select": "*",
                "execution_id": f"in.({','.join(execution_ids)})",
                "state": "neq.failed",
            },
        )

    async def reconcile_entry_fills(self, client: DeltaClient, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                slippage = slippage_fields(str(order.get("side")), order.get("reference_price"), average_fill_price)
                order = {
                    **order,
                    "filled_size": str(filled_size),
                    "average_fill_price": str(average_fill_price),
                    "commission": str(commission),
                    **slippage,
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
                if slippage:
                    await self.write_audit("execution_orders", slippage, {"id": f"eq.{order['id']}"})
            reconciled.append(order)
        return reconciled

    async def contract_value(self, client: DeltaClient, symbol: str) -> Decimal:
        if symbol not in self.contract_values:
            product = await client.product(symbol)
            self.contract_values[symbol] = decimal_value(product.get("result", {}).get("contract_value"), "1")
        return self.contract_values[symbol]

    async def run_executions(self, strategy_id: str) -> list[dict[str, Any]]:
        return await self.db.select(
            "executions",
            {
                "select": "id,kind,status,error,started_at,completed_at",
                "strategy_id": f"eq.{strategy_id}",
                "order": "started_at.asc",
            },
        )

    async def run_orders(
        self, strategy_id: str, executions: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """Every order the run placed, entry and exit, tagged with its phase."""
        executions = executions if executions is not None else await self.run_executions(strategy_id)
        kinds = {str(item["id"]): str(item["kind"]) for item in executions}
        if not kinds:
            return []
        rows = await self.db.select(
            "execution_orders",
            {"select": "*", "execution_id": f"in.({','.join(kinds)})", "order": "created_at.asc"},
        )
        return [{**row, "kind": kinds.get(str(row["execution_id"]), "entry")} for row in rows]

    async def enrich_contract_values(self, client: DeltaClient, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Backfill the per-lot contract value on recorded orders so premium maths
        is exact. Delta's product endpoint is public and the result is cached, so
        this costs at most one request per symbol per process.
        """
        resolved: dict[str, Decimal] = {}
        for symbol in sorted({str(order["product_symbol"]) for order in orders if not order.get("contract_value")}):
            try:
                resolved[symbol] = await self.contract_value(client, symbol)
            except Exception as exc:
                logger.warning("Could not read the contract value for %s: %s", symbol, exc)

        enriched: list[dict[str, Any]] = []
        for order in orders:
            value = resolved.get(str(order.get("product_symbol")))
            if order.get("contract_value") or value is None:
                enriched.append(order)
                continue
            if order.get("id"):
                await self.write_audit("execution_orders", {"contract_value": str(value)}, {"id": f"eq.{order['id']}"})
            enriched.append({**order, "contract_value": str(value)})
        return enriched

    async def record_settlement(self, client: DeltaClient, strategy_id: str) -> dict[str, Any] | None:
        """
        Persist the money view of a finished run. Reporting only: a failure here
        is logged and never changes the execution outcome.
        """
        try:
            orders = await self.enrich_contract_values(client, await self.run_orders(strategy_id))
            summary = settlement_summary(orders)
        except Exception as exc:
            logger.warning("Could not summarise the settlement for strategy %s: %s", strategy_id, exc)
            return None
        summary["settledAt"] = iso_now()
        await self.write_audit(
            "strategies",
            {
                "result_json": summary,
                "realized_pnl": summary["realizedPnl"] if decimal_value(summary["closedLots"]) > 0 else None,
            },
            {"id": f"eq.{strategy_id}"},
        )
        return summary

    async def run_detail(self, strategy_id: str, user_id: str) -> dict[str, Any]:
        """
        Everything recorded about a single run: schedule, criteria, per-leg
        fills with slippage, settlement, risk monitor state, and raw responses.
        """
        rows = await self.db.select(
            "strategies",
            {"select": "*", "id": f"eq.{strategy_id}", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        if not rows:
            raise AppError(404, "Strategy not found", "strategy_not_found")
        row = rows[0]
        executions = await self.run_executions(strategy_id)
        orders = await self.run_orders(strategy_id, executions)
        if orders and any(not order.get("contract_value") for order in orders):
            client = DeltaClient(self.settings)
            try:
                orders = await self.enrich_contract_values(client, orders)
            finally:
                await client.close()

        stored = row.get("result_json") or {}
        settlement = settlement_summary(orders) if orders else {}
        if stored.get("settledAt"):
            settlement["settledAt"] = stored["settledAt"]
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "entryAt": row.get("entry_at"),
            "exitAt": row.get("exit_at"),
            "entryExecutedAt": row.get("entry_execution_at"),
            "exitExecutedAt": row.get("exit_execution_at"),
            "lastError": row.get("last_error"),
            "definition": row.get("definition_json") or {},
            "savedStrategyId": row.get("saved_strategy_id"),
            "riskState": row.get("risk_state") or {},
            "riskMonitoredAt": row.get("risk_monitor_at"),
            "combinedStopTriggeredAt": row.get("combined_stop_triggered_at"),
            "settlement": settlement,
            "executions": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "status": item["status"],
                    "error": item.get("error"),
                    "startedAt": item.get("started_at"),
                    "completedAt": item.get("completed_at"),
                }
                for item in executions
            ],
            "orders": [
                {
                    "id": order["id"],
                    "kind": order.get("kind"),
                    "legId": order.get("leg_id"),
                    "deltaOrderId": order.get("delta_order_id"),
                    "clientOrderId": order.get("client_order_id"),
                    "productId": order.get("product_id"),
                    "productSymbol": order.get("product_symbol"),
                    "side": order.get("side"),
                    "size": str(order.get("size")),
                    "filledSize": str(order.get("filled_size") or "0"),
                    "averageFillPrice": order.get("average_fill_price"),
                    "referencePrice": order.get("reference_price"),
                    "slippage": order.get("slippage"),
                    "slippagePercent": order.get("slippage_percent"),
                    "contractValue": order.get("contract_value"),
                    "orderType": order.get("order_type"),
                    "limitPrice": order.get("limit_price"),
                    "commission": str(order.get("commission") or "0"),
                    "state": order.get("state"),
                    "createdAt": order.get("created_at"),
                    "response": order.get("response_json") or {},
                }
                for order in orders
            ],
        }

    async def delete_strategy(self, strategy_id: str, user_id: str) -> None:
        """
        Remove a finished run and its execution audit trail. Refused while the
        run could still hold an open Delta position, because deleting the record
        would leave that position untracked.
        """
        rows = await self.db.select(
            "strategies",
            {
                "select": "id,status,entry_execution_at,exit_execution_at",
                "id": f"eq.{strategy_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        if not rows:
            raise AppError(404, "Strategy not found", "strategy_not_found")
        row = rows[0]
        status = str(row["status"])
        if status in {"executing_entry", "executing_exit"}:
            raise AppError(409, "This run is executing right now. Wait for it to finish", "cannot_delete_running")
        if status == "active":
            raise AppError(409, "Exit this live run before deleting it from history", "cannot_delete_live")
        if status == "attention" and row.get("entry_execution_at") and not row.get("exit_execution_at"):
            raise AppError(
                409,
                "This run entered but never confirmed an exit, so it may still hold a position. "
                "Exit it before deleting the record",
                "cannot_delete_unresolved",
            )
        if not await self.db.delete(
            "strategies", {"select": "id", "id": f"eq.{strategy_id}", "user_id": f"eq.{user_id}"}
        ):
            raise AppError(409, "The run could not be deleted", "delete_failed")

    async def monitor_combined_strategy(self, row: dict[str, Any]) -> bool:
        definition = StrategyDefinition.model_validate(row["definition_json"])
        if definition.riskMode not in {"combined_premium", "strategy_level"}:
            return False
        client = await self.client_for_user(str(row["user_id"]))
        try:
            orders = await self.entry_orders(str(row["id"]))
            orders = await self.reconcile_entry_fills(client, orders)
            ready = bool(orders) and all(
                decimal_value(order.get("filled_size")) >= decimal_value(order.get("size")) for order in orders
            )
            risk_state: dict[str, Any] = {
                "mode": "strategy_level",
                "status": "armed" if ready else "awaiting_fills",
                "riskBasis": definition.riskBasis,
                "stopPercent": str(definition.stopLossPercent),
                "takeProfitPercent": str(definition.takeProfitPercent),
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
            metrics = strategy_level_metrics(
                priced_legs,
                risk_basis=definition.riskBasis,
                stop_percent=decimal_value(definition.stopLossPercent),
                take_profit_percent=decimal_value(definition.takeProfitPercent),
            )
            entry_value = metrics["entry_value"]
            current_value = metrics["current_value"]
            if not isinstance(entry_value, Decimal) or entry_value <= 0:
                risk_state.update({"status": "attention", "message": "The strategy entry risk basis is not positive"})
                await self.db.update(
                    "strategies",
                    {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                    {"id": f"eq.{row['id']}", "status": "eq.active"},
                )
                return False

            risk_state.update(
                {
                    "entryValue": str(entry_value),
                    "currentValue": str(current_value),
                    "currentValueLabel": metrics["current_label"],
                    "stopValue": str(metrics["stop_value"]),
                    "targetValue": str(metrics["target_value"]),
                    "profit": str(metrics["profit"]),
                    "returnPercent": str(Decimal(str(metrics["profit"])) / entry_value * 100),
                    "pricedAt": iso_now(),
                }
            )
            await self.db.update(
                "strategies",
                {"risk_state": risk_state, "risk_monitor_at": iso_now()},
                {"id": f"eq.{row['id']}", "status": "eq.active"},
            )
            trigger_reason = (
                "stop_loss" if metrics["stop_triggered"] else "take_profit" if metrics["target_triggered"] else None
            )
            if not trigger_reason:
                return False

            triggered_at = iso_now()
            risk_state["status"] = "triggered"
            risk_state["exitReason"] = trigger_reason
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
        if row.get("status") == "completed":
            raise AppError(409, "Strategy exit has already run", "already_exited")
        client = await self.client_for_user(str(row["user_id"]))
        try:
            recorded_orders = await self.entry_orders(strategy_id)
            if not preclaimed:
                await self.claim_exit(strategy_id)
            elif row.get("status") != "executing_exit":
                raise AppError(409, "Combined stop exit was not claimed", "exit_not_claimed")
            executions = await self.db.insert(
                "executions", {"strategy_id": strategy_id, "kind": "exit", "status": "running"}
            )
            if not executions:
                await self.db.update(
                    "strategies",
                    {
                        "status": "attention",
                        "exit_execution_at": None,
                        "last_error": "Could not start the exit execution record",
                    },
                    {"id": f"eq.{strategy_id}", "status": "eq.executing_exit"},
                )
                raise AppError(500, "Could not start the execution record", "execution_record_failed")
            execution_id = str(executions[0]["id"])
            failures: list[Exception] = []
            submitted = 0
            verified = 0

            if not recorded_orders:
                failures.append(AppError(409, "No recorded entry orders are available to exit", "entry_orders_missing"))
            else:
                product_ids = sorted({int(item["product_id"]) for item in recorded_orders})
                try:
                    open_responses = await asyncio.gather(
                        *(
                            client.open_orders(product_ids[index : index + 10])
                            for index in range(0, len(product_ids), 10)
                        )
                    )
                    open_order_ids = {
                        str(item.get("id"))
                        for response in open_responses
                        for item in (response.get("result") or [])
                        if item.get("id") is not None
                    }
                    for item in recorded_orders:
                        delta_order_id = str(item.get("delta_order_id") or "")
                        if delta_order_id and delta_order_id in open_order_ids:
                            await client.cancel_order(int(delta_order_id), int(item["product_id"]))
                    recorded_orders = await self.reconcile_entry_fills(client, recorded_orders)
                except Exception as exc:
                    failures.append(
                        AppError(
                            502,
                            f"Could not cancel or reconcile outstanding entry orders: {exc}",
                            "entry_order_cleanup_failed",
                        )
                    )

            products: dict[int, dict[str, Any]] = {}
            for item in recorded_orders:
                product_id = int(item["product_id"])
                filled = decimal_value(item.get("filled_size"))
                owned_size = filled
                if owned_size <= 0 and str(item.get("state")) == "closed":
                    owned_size = decimal_value(item["size"])
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

            async def close_product(index: int, product_id: int, product: dict[str, Any]) -> None:
                nonlocal submitted, verified
                try:
                    live_size = await self.live_position_size(client, product_id)
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
                    client_order_id = f"dx_{strategy_id[:8]}_{index}_{base36(int(time.time() * 1000))}"[:32]
                    # Slippage baseline for the close. Never allowed to delay or
                    # block the exit itself.
                    reference_price: str | None = None
                    with suppress(Exception):
                        ticker = await client.ticker(str(product["product_symbol"]))
                        mark = decimal_value((ticker.get("result") or {}).get("mark_price"))
                        reference_price = str(mark) if mark > 0 else None
                    order = await self.place_reduce_only_close(
                        client,
                        product_id,
                        close_size,
                        live_size,
                        client_order_id,
                        str(product["product_symbol"]),
                    )
                    submitted += 1
                    result = order.get("result") or {}
                    requested_size = Decimal(int(close_size))
                    unfilled_size = decimal_value(result.get("unfilled_size"), str(requested_size))
                    close_side = "sell" if live_size > 0 else "buy"
                    await self.record_order(
                        {
                            "execution_id": execution_id,
                            "leg_id": "exit",
                            "delta_order_id": str(result.get("id") or ""),
                            "client_order_id": client_order_id,
                            "product_id": product_id,
                            "product_symbol": product["product_symbol"],
                            "side": close_side,
                            "size": int(close_size),
                            "filled_size": str(max(Decimal("0"), requested_size - unfilled_size)),
                            "average_fill_price": result.get("average_fill_price"),
                            "commission": str(decimal_value(result.get("paid_commission") or result.get("commission"))),
                            "state": str(result.get("state") or "submitted"),
                            "order_type": "market_order",
                            "reference_price": reference_price,
                            "response_json": order,
                            **slippage_fields(close_side, reference_price, result.get("average_fill_price")),
                        }
                    )
                    remaining_size = await self.verify_position_reduction(client, product_id, close_size, live_size)
                    verified += 1
                    await self.db.update(
                        "execution_orders",
                        {
                            "state": "verified_closed",
                            "response_json": {
                                **order,
                                "verification": {
                                    "initialSize": str(live_size),
                                    "remainingSize": str(remaining_size),
                                    "verifiedAt": iso_now(),
                                },
                            },
                        },
                        {"client_order_id": f"eq.{client_order_id}"},
                    )
                except Exception as exc:
                    failures.append(exc)

            if not failures:
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
                risk_state["status"] = "attention" if failure else "exit_verified"
                risk_state["exitVerifiedAt" if not failure else "exitFailedAt"] = completed
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
                        "exit_execution_at": None if failure else completed,
                        "last_error": str(failure) if failure else None,
                        "risk_state": risk_state,
                    },
                    {"id": f"eq.{strategy_id}"},
                ),
            )
            settlement = await self.record_settlement(client, strategy_id)
            if failure:
                raise failure
            await self.release_capital_slot(str(row["user_id"]), strategy_id)
            return {
                "executionId": execution_id,
                "ordersSubmitted": submitted,
                "positionsVerified": verified,
                "verified": True,
                "settlement": settlement,
            }
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
                await self.reject_scheduled_entry(
                    str(row["id"]),
                    AppError(
                        409,
                        f"the execution window expired after {int(lateness)} seconds",
                        "entry_window_expired",
                    ),
                )
                continue
            try:
                await self.execute_entry(str(row["id"]))
            except AppError as error:
                if error.code in TERMINAL_SCHEDULED_ENTRY_CODES:
                    await self.reject_scheduled_entry(str(row["id"]), error)
                    logger.warning(
                        "Scheduled entry rejected for strategy %s code=%s message=%s",
                        row["id"],
                        error.code,
                        error.message,
                    )
                    continue
                logger.exception("Scheduled entry failed for strategy %s", row["id"])
            except Exception:
                logger.exception("Scheduled entry failed for strategy %s", row["id"])
        for row in due_exits:
            try:
                await self.execute_exit(str(row["id"]))
            except Exception:
                logger.exception("Scheduled exit failed for strategy %s", row["id"])

    async def reject_scheduled_entry(self, strategy_id: str, error: AppError) -> None:
        reason = f"Entry not placed: {error.message}"
        rows = await self.db.update(
            "strategies",
            {"status": "attention", "last_error": reason},
            {"id": f"eq.{strategy_id}", "status": "eq.scheduled", "entry_execution_at": "is.null"},
        )
        if not rows:
            return
        await self.write_audit(
            "strategy_proposals",
            {"status": "rejected", "rejection_reason": reason},
            {"strategy_id": f"eq.{strategy_id}", "status": "eq.scheduled"},
        )


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
