"""Reasons an entry was not placed, independent of live-position alerts."""

from typing import Any

import httpx

from .errors import AppError


def entry_failure(error: Exception, occurred_at: str) -> dict[str, Any]:
    code = error.code if isinstance(error, AppError) else "system_error"
    category = "system"
    if code == "capital_slots_full":
        category = "slots_full"
    elif code == "capital_reserved":
        category = "capital_full"
    elif code == "btc_entry_priority":
        category = "asset_priority"
    elif code in {
        "automation_balance_unavailable", "insufficient_funds", "insufficient_balance", "insufficient_margin"
    }:
        category = "low_balance"
    elif code in {"automatic_lot_too_large", "manual_lots_exceed_capital_budget"}:
        category = "capital_budget"
    elif code == "delta_unreachable" or isinstance(error, (httpx.TransportError, TimeoutError, ConnectionError)):
        category = "network"
        if not isinstance(error, AppError):
            code = "network_error"
    elif code in {"not_connected", "delta_not_connected", "automation_disabled", "saved_strategy_version_changed"}:
        category = "authorization"
    elif code in {"option_chain_empty", "spot_price_missing", "strike_not_found"}:
        category = "market_data"
    elif code == "entry_window_expired":
        category = "window_expired"
    elif code == "activation_recheck_failed":
        category = "not_reconfirmed"
    elif isinstance(error, AppError) and error.status < 500:
        category = "configuration"
    if isinstance(error, AppError):
        message = error.message
    elif category == "network":
        message = "Network request failed"
    else:
        message = "Entry failed because of a system error"
    return {"code": code, "category": category, "message": message, "occurredAt": occurred_at}
