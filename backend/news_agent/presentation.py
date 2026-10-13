"""Model-facing evidence display. Never use these rounded values for execution."""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# These fields are normalized to milliseconds by the market services.
MILLISECOND_FIELDS = frozenset({
    "capturedAt", "observedAt", "receivedAt", "eventTime", "firstTradeAt", "lastTradeAt",
    "firstOpenTime", "lastCloseTime", "openTime", "closeTime", "nextFundingAt", "asOf", "expiryMs", "lastEventAt",
})
DATETIME_FIELDS = frozenset({
    "start", "end", "asOf", "expiry", "activationTime", "exitTime", "activation_time", "exit_at",
    "entryAt", "exitAt", "scheduledForUtc", "scheduledForIst", "scheduled_for", "lastObservationAt",
    "published_at", "retrieved_at", "publishedAt", "fetched_at", "feed_published_at", "date",
    "scheduledFor", "runTime", "startedAt", "completedAt", "started_at", "completed_at", "entry_at",
    "expiresAt", "created_at", "updated_at",
})
EXACT_FIELDS = frozenset({
    "definition", "definition_json", "definitionJson", "capitalPolicy", "contractMultiplier", "multiplier",
})
NUMERIC_TEXT_FIELDS = frozenset({
    "lastPrice", "priceChange", "priceChangePercent", "highPrice", "lowPrice", "baseVolume", "quoteVolume",
    "bestBid", "bestAsk", "bidSizeContracts", "askSizeContracts", "mark", "spot", "strike",
})


def format_number(value: int | float | Decimal, *, exact: bool = False) -> str:
    """Two decimals, extending to the first nonzero decimal for tiny values."""
    number = Decimal(str(value))
    if not number.is_finite():
        return "Unavailable"
    if exact or isinstance(value, int):
        return str(value)
    places = max(2, -number.copy_abs().adjusted()) if number else 2
    with localcontext() as context:
        context.prec = max(28, len(number.as_tuple().digits) + places + max(0, number.adjusted()) + 2)
        rounded = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if not rounded:
        rounded = rounded.copy_abs()
    text = format(rounded, "f")
    if places > 2:
        text = text.rstrip("0")
        if len(text.rsplit(".", 1)[-1]) < 2:
            text += "0" * (2 - len(text.rsplit(".", 1)[-1]))
    return text


def format_time(value: Any, *, milliseconds: bool = False) -> str | None:
    """Convert known instants only; undated and timezone-free text stays unchanged."""
    try:
        if milliseconds and not isinstance(value, bool):
            timestamp = Decimal(str(value))
            if not timestamp.is_finite() or timestamp <= 0:
                return None
            moment = datetime.fromtimestamp(float(timestamp / 1000), UTC)
        elif isinstance(value, datetime):
            moment = value
        elif isinstance(value, str):
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if moment.utcoffset() is None:
            return None
        local = moment.astimezone(IST)
        text = local.strftime("%Y-%m-%d %H:%M:%S")
        if local.microsecond:
            text += "." + f"{local.microsecond:06d}".rstrip("0")
        return text + " IST"
    except (ValueError, TypeError, InvalidOperation, OverflowError, OSError):
        return None


def _scalar(value: Any, key: str, path: tuple[str, ...], exact: bool) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "true" if value else "false"
    # start/end are epochs only inside the documented market bucket/flow structures.
    bucket_time = key in {"start", "end"} and any(
        part in {"previousLiquidityBucket", "recentTradeFlow", "liquidity", "buckets"} for part in path
    ) and isinstance(value, (int, float))
    known_epoch = key in MILLISECOND_FIELDS and isinstance(value, (int, float, Decimal))
    known_date = key in (DATETIME_FIELDS | MILLISECOND_FIELDS) and isinstance(value, (str, datetime))
    if known_epoch or known_date or isinstance(value, datetime) or bucket_time:
        rendered = format_time(value, milliseconds=known_epoch or bucket_time)
        if rendered:
            return rendered
        if known_epoch or bucket_time:
            return "Time unavailable"
    if isinstance(value, (int, float, Decimal)):
        return format_number(value, exact=exact)
    if isinstance(value, str) and key in NUMERIC_TEXT_FIELDS and not exact:
        try:
            return format_number(Decimal(value))
        except InvalidOperation:
            pass
    return str(value)


def markdown_evidence(value: Any, title: str = "Evidence", *, exact: bool = False) -> str:
    """Render complete evidence as headings and lists, preserving text and field units."""
    lines = [f"## {title}", ""]

    def render(item: Any, key: str, path: tuple[str, ...], depth: int, preserve: bool) -> None:
        indent = "  " * depth
        preserve = preserve or key in EXACT_FIELDS
        if isinstance(item, (dict, list, tuple)) and item:
            if key:
                lines.append(f"{indent}- {key}:")
                depth += 1
            if isinstance(item, dict):
                for field, child in item.items():
                    if field == "scheduledForUtc" and "scheduledForIst" in item:
                        continue
                    if field == "scheduledForUtc":
                        field = "scheduledForIst"
                    render(child, str(field), (*path, key), depth, preserve)
            else:
                for index, child in enumerate(item, 1):
                    render(child, f"Item {index}", (*path, key), depth, preserve)
        else:
            text = "None listed" if isinstance(item, (dict, list, tuple)) else _scalar(item, key, path, preserve)
            # Indent source text as data, keeping original claims and numbers intact.
            parts = text.splitlines() or [""]
            label = f"{key}: " if key else ""
            lines.append(f"{indent}- {label}{parts[0]}")
            lines.extend(f"{indent}  {part}" for part in parts[1:])

    render(value, "", (), 0, exact)
    return "\n".join(lines)
