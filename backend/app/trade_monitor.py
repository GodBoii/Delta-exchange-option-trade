"""Read-only trade observations from recorded fills and existing price streams."""

from datetime import UTC, datetime
from typing import Any, Literal

PRICE_MAX_AGE_SECONDS = 15.0


def monitor_snapshot(
    engine: Any, user_id: str, detail: dict[str, Any], *, source: Literal["live", "archive"] = "live"
) -> dict[str, Any]:
    # The caller has already authorized and loaded this specific user's run.
    # Never create an exchange session or reconcile orders as a side effect of viewing.
    session = getattr(engine, "sessions", {}).get(user_id) if source == "live" else None
    events = session.events if session is not None else None
    symbols = sorted({str(order["productSymbol"]) for order in detail["orders"] if order.get("productSymbol")})
    quotes = [
        {
            "symbol": symbol,
            "markPrice": events.watch_mark(symbol, PRICE_MAX_AGE_SECONDS)
            if events is not None and not events.closed else None,
        }
        for symbol in symbols
    ]
    return {
        "success": True,
        "source": source,
        "observedAt": datetime.now(UTC).isoformat(),
        "privateStream": "live" if events is not None and not events.closed
        and events.connected["private"] and events.portfolio.ready else "unavailable",
        "quotes": quotes,
        "run": detail,
    }
