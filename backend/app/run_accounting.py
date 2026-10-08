"""Which software runs count toward P&L, and the sanitized copy the owner ledger keeps.

``classify_run`` is the single inclusion rule. Personal and owner summaries both
aggregate the ``accounting_state`` it assigns; only ``settled`` runs contribute to
net P&L, wins and losses.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from .settlement import decimal_value, filled_size, optional_decimal, run_settlement

AccountingState = Literal["settled", "open", "scheduled", "cancelled", "incomplete", "attention"]
ACCOUNTING_STATES: tuple[AccountingState, ...] = (
    "settled",
    "open",
    "scheduled",
    "cancelled",
    "incomplete",
    "attention",
)
LIVE_STATUSES = frozenset({"executing_entry", "active", "executing_exit"})
# Order fields worth keeping for later analysis. Raw exchange responses are dropped.
ORDER_FIELDS = {
    "id": "id",
    "kind": "kind",
    "leg_id": "legId",
    "delta_order_id": "deltaOrderId",
    "client_order_id": "clientOrderId",
    "product_id": "productId",
    "product_symbol": "productSymbol",
    "side": "side",
    "average_fill_price": "averageFillPrice",
    "reference_price": "referencePrice",
    "slippage": "slippage",
    "slippage_percent": "slippagePercent",
    "contract_value": "contractValue",
    "order_type": "orderType",
    "limit_price": "limitPrice",
    "state": "state",
    "created_at": "createdAt",
}
RISK_STATE_FIELDS = ("mode", "exposureStatus", "closureReason", "reconciledAt", "exclusiveFillAccounting")
POLICY_FIELDS = (
    "allocationMode",
    "capitalAmount",
    "maximumConcurrentStrategies",
    "totalBalanceAtEntry",
    "availableBalanceAtEntry",
)


@dataclass(frozen=True, slots=True)
class RunAccounting:
    state: AccountingState
    reason: str | None
    settlement: dict[str, Any]
    realized_pnl: Decimal | None
    gross_pnl: Decimal | None
    exchange_fees: Decimal | None
    entry_premium: Decimal | None
    exit_premium: Decimal | None


def classify_run(status: str, stored_settlement: dict[str, Any], orders: list[dict[str, Any]]) -> RunAccounting:
    """
    Assign the accounting state of one run.

    A run is ``settled`` only when it completed, every entry lot was closed,
    no order has an unknown fill state, fees are final and a realized P&L exists.
    Open positions never count, so premium received on entry is not profit.
    """
    has_contract_values = all(
        order.get("contract_value") for order in orders if filled_size(order) > 0
    )
    exchange_basis = stored_settlement.get("accountingBasis") == "allocated_exchange_fills"
    if exchange_basis or has_contract_values or stored_settlement.get("realizedPnl") is None:
        settlement = run_settlement(stored_settlement, orders)
    else:
        # Orders predate contract-value enrichment; the stored summary was computed with it.
        settlement = dict(stored_settlement)

    def result(state: AccountingState, reason: str | None) -> RunAccounting:
        settled = state == "settled"
        return RunAccounting(
            state=state,
            reason=reason,
            settlement=settlement,
            realized_pnl=optional_decimal(settlement.get("realizedPnl")) if settled else None,
            gross_pnl=optional_decimal(settlement.get("grossPnl")),
            exchange_fees=optional_decimal(settlement.get("commission")),
            entry_premium=optional_decimal(settlement.get("entryPremium")),
            exit_premium=optional_decimal(settlement.get("exitPremium")),
        )

    filled = decimal_value(settlement.get("filledLots"))
    if status in {"draft", "scheduled"}:
        return result("scheduled", None)
    if status in LIVE_STATUSES:
        return result("open", "position_open")
    if status == "attention":
        return result("attention", "needs_attention")
    if status == "skipped":
        return result("incomplete", "skipped_after_fill") if filled > 0 else result("cancelled", "entry_not_placed")
    if status == "cancelled":
        return result("incomplete", "cancelled_after_fill") if filled > 0 else result("cancelled", None)
    if status != "completed":
        return result("incomplete", "unknown_status")
    if any(str(order.get("state")) == "unknown" for order in orders):
        return result("incomplete", "fill_state_unknown")
    if filled <= 0:
        return result("incomplete", "no_fills")
    if not settlement.get("fullyClosed"):
        return result("incomplete", "position_not_fully_closed")
    if exchange_basis and not settlement.get("accountingComplete"):
        return result("incomplete", "fees_pending")
    if not exchange_basis and not has_contract_values and stored_settlement.get("realizedPnl") is None:
        # Rebuilding without contract values would price every lot as one unit.
        return result("incomplete", "contract_value_missing")
    if optional_decimal(settlement.get("realizedPnl")) is None:
        return result("incomplete", "realized_pnl_missing")
    return result("settled", None)


def order_payload(order: dict[str, Any], *, include_response: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {name: order.get(field) for field, name in ORDER_FIELDS.items()}
    payload["size"] = str(order.get("size"))
    payload["filledSize"] = str(order.get("filled_size") or "0")
    payload["commission"] = str(order.get("commission") or "0")
    if include_response:
        payload["response"] = order.get("response_json") or {}
    return payload


def run_detail_payload(
    row: dict[str, Any],
    executions: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    settlement: dict[str, Any],
    *,
    include_raw: bool,
) -> dict[str, Any]:
    """The run detail wire shape. ``include_raw=False`` is the sanitized owner copy."""
    risk_state = row.get("risk_state") or {}
    policy = row.get("capital_policy_json") or {}
    return {
        "id": row["id"],
        "name": row.get("name"),
        "status": row.get("status"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "entryAt": row.get("entry_at"),
        "exitAt": row.get("exit_at"),
        "entryExecutedAt": row.get("entry_execution_at"),
        "exitExecutedAt": row.get("exit_execution_at"),
        "lastError": row.get("last_error"),
        "entryOutcome": row.get("entry_outcome"),
        "definition": row.get("definition_json") or {},
        "savedStrategyId": row.get("saved_strategy_id"),
        "capitalSlot": row.get("capital_slot"),
        "capitalBudget": str(row.get("capital_budget")) if row.get("capital_budget") is not None else None,
        "capitalPolicy": policy if include_raw else {key: policy[key] for key in POLICY_FIELDS if key in policy},
        "riskState": risk_state
        if include_raw
        else {key: risk_state[key] for key in RISK_STATE_FIELDS if key in risk_state},
        "riskMonitoredAt": row.get("risk_monitor_at"),
        "combinedStopTriggeredAt": row.get("combined_stop_triggered_at"),
        "settlement": settlement,
        "executions": [
            {
                "id": item["id"],
                "kind": item.get("kind"),
                "status": item.get("status"),
                "error": item.get("error"),
                "startedAt": item.get("started_at"),
                "completedAt": item.get("completed_at"),
            }
            for item in executions
        ],
        "orders": [order_payload(order, include_response=include_raw) for order in orders],
    }


def activity_time(row: dict[str, Any]) -> Any:
    """The date a run is filed under: exit for closed runs, otherwise entry or creation."""
    return (
        row.get("exit_execution_at")
        or row.get("entry_execution_at")
        or row.get("entry_at")
        or row.get("created_at")
    )
