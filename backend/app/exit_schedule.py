"""Resolve an exit decision and a listed option expiry before a trade is scheduled."""

from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator

from .models import StrategyDefinition

if TYPE_CHECKING:
    from .delta import DeltaClient

IST = ZoneInfo("Asia/Kolkata")
SESSION_END = time(17, 30)
PRESETS: dict[str, frozenset[int]] = {
    "intraday": frozenset({7, 11}),
}


def session_expiry(reference: datetime) -> datetime:
    """The next 17:30 IST settlement, never a later listed expiry fallback."""
    if reference.utcoffset() is None:
        raise ValueError("Session reference must include a timezone")
    local = reference.astimezone(IST)
    end = datetime.combine(local.date(), SESSION_END, IST)
    if local >= end:
        end += timedelta(days=1)
    return end.astimezone(UTC)


def maximum_hold_hours(trigger: str | None = None) -> int:
    return 11 if trigger == "new_york_session" else 7


def validate_session_schedule(
    definition: dict[str, Any], *, trigger: str | None = None, review_at: datetime | None = None
) -> None:
    """Validate new decisions without changing historical or already active definitions."""
    entry = _instant((definition.get("entry") or {}).get("entryAt"))
    exit_at = _instant((definition.get("entry") or {}).get("exitAt"))
    if entry is None or exit_at is None:
        raise ValueError("A timezone-aware entry and exit are required")
    expiry = session_expiry(review_at or entry)
    cutoff = expiry - timedelta(minutes=int(definition.get("exitMinutesBeforeExpiry") or 5))
    if session_expiry(entry) != expiry or not entry < exit_at <= cutoff:
        raise ValueError("Entry and exit must remain inside the current options session and expiry buffer")
    if exit_at - entry > timedelta(hours=maximum_hold_hours(trigger)):
        raise ValueError("Planned hold exceeds this review's maximum duration")
    date = expiry.astimezone(IST).date().isoformat()
    if any(leg.get("expiry") != date for leg in definition.get("legs") or []):
        raise ValueError("Every leg must use the upcoming session expiry")


async def fetch_option_catalog(client: DeltaClient, underlying: str) -> list[dict[str, Any]]:
    """Read Delta's live products and authoritative settlement times for manual schedules."""
    response = await client.request(
        "GET",
        "/v2/tickers",
        query={"contract_types": "call_options,put_options", "underlying_asset_symbols": underlying},
    )
    rows = response.get("result")
    if not isinstance(rows, list):
        raise ValueError("Delta option catalog is unavailable")
    representatives: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        match = re.search(r"-(\d{6})$", symbol)
        if match and f"-{underlying}-" in symbol:
            representatives.setdefault(match.group(1), symbol)

    async def settlement(code: str, symbol: str) -> tuple[str, str | None]:
        product = await client.request("GET", f"/v2/products/{quote(symbol, safe='')}")
        value = product.get("result")
        return code, str(value.get("settlement_time")) if isinstance(value, dict) and value.get(
            "settlement_time"
        ) else None

    times = dict(await asyncio.gather(*(settlement(code, symbol) for code, symbol in representatives.items())))
    options = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        match = re.search(r"-(\d{6})$", symbol)
        expiry = times.get(match.group(1)) if match else None
        if expiry:
            options.append(
                {
                    "symbol": symbol,
                    "expiry": expiry,
                    "strike": row.get("strike_price"),
                    "spot": row.get("spot_price"),
                }
            )
    return options


class ExitChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["intraday", "specific_time"]
    hours: int | None = None
    exit_at: datetime | None = None

    @model_validator(mode="after")
    def validate_choice(self) -> ExitChoice:
        if self.kind in PRESETS:
            if self.hours not in PRESETS[self.kind] or self.exit_at is not None:
                raise ValueError(f"{self.kind} requires one of {sorted(PRESETS[self.kind])} hours only")
        elif self.exit_at is None or self.exit_at.utcoffset() is None or self.hours is not None:
            raise ValueError("specific_time requires a timezone-aware exit_at only")
        return self


def _instant(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None


def session_number(value: datetime) -> int:
    local = value.astimezone(IST)
    day = local.date().toordinal()
    return day if local.timetz().replace(tzinfo=None) >= SESSION_END else day - 1


def _strike_available(leg: dict[str, Any], options: list[dict[str, Any]]) -> bool:
    kind = "call" if leg.get("optionType") == "call" else "put"
    candidates: list[tuple[float, dict[str, Any]]] = []
    for option in options:
        symbol = str(option.get("symbol") or "")
        if not symbol.startswith("C-" if kind == "call" else "P-"):
            continue
        try:
            candidates.append((float(option["strike"]), option))
        except (KeyError, TypeError, ValueError):
            continue
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return False
    if leg.get("strikeMode") == "exact":
        return any(strike == float(leg.get("exactStrike") or 0) for strike, _ in candidates)
    try:
        spot = float(next(option["spot"] for _, option in candidates if option.get("spot") is not None))
    except (StopIteration, TypeError, ValueError):
        return False
    atm = min(range(len(candidates)), key=lambda index: abs(candidates[index][0] - spot))
    mode = leg.get("strikeMode")
    direction = 0 if mode == "atm" else (1 if (kind == "call") == (mode == "otm") else -1)
    index = atm + direction * int(leg.get("strikeSteps") or 0)
    return 0 <= index < len(candidates)


def _listed_expiries(template: dict[str, Any], options: list[dict[str, Any]]) -> list[datetime]:
    if not options:
        raise ValueError("Delta returned no listed options for this asset")
    underlying = (template.get("instrument") or {}).get("underlying")
    by_expiry: dict[datetime, list[dict[str, Any]]] = {}
    for option in options:
        if not isinstance(option, dict):
            continue
        symbol = str(option.get("symbol") or "")
        expiry = _instant(option.get("expiry"))
        if expiry is None or f"-{underlying}-" not in symbol:
            continue
        by_expiry.setdefault(expiry, []).append(option)
    legs = template.get("legs") or []
    return sorted(expiry for expiry, chain in by_expiry.items() if all(_strike_available(leg, chain) for leg in legs))


def template_from_definition(value: dict[str, Any]) -> dict[str, Any]:
    """Discard schedule and expiry fields while retaining the strategy's trading rules."""
    template = deepcopy(value)
    template["schemaVersion"] = 3
    template["sameExpiryRequired"] = True
    for key in ("entry", "holdingMode", "expiryPolicy", "selectionCriteria"):
        template.pop(key, None)
    for leg in template.get("legs") or []:
        leg.pop("expiry", None)
    return template


def validate_template(value: dict[str, Any]) -> dict[str, Any]:
    template = template_from_definition(value)
    probe = deepcopy(template)
    probe["schemaVersion"] = 2
    probe["holdingMode"] = "intraday"
    probe["expiryPolicy"] = "auto"
    probe["entry"] = {
        "strategyType": "intraday",
        "entryAt": "2030-01-01T00:00:00+00:00",
        "exitAt": "2030-01-01T01:00:00+00:00",
    }
    for leg in probe.get("legs") or []:
        leg["expiry"] = "2030-01-02"
    StrategyDefinition.model_validate(probe)
    return template


def resolve_exit_schedule(
    template: dict[str, Any], *, entry_at: datetime, choice: ExitChoice, options: list[dict[str, Any]],
    trigger: str | None = None, review_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an immutable live v2 definition and an explanation of its schedule."""
    if entry_at.utcoffset() is None:
        raise ValueError("Entry time must include a timezone")
    entry = entry_at.astimezone(UTC)
    cutoff = timedelta(minutes=int(template.get("exitMinutesBeforeExpiry") or 5))
    expiry = session_expiry(review_at or entry)
    if session_expiry(entry) != expiry:
        raise ValueError("Activation must remain in the reviewed options session")
    if expiry not in _listed_expiries(template, options):
        raise ValueError("Upcoming session expiry does not support every leg and strike rule")
    limit = maximum_hold_hours(trigger)
    if choice.kind == "intraday":
        if int(choice.hours or 0) > limit:
            raise ValueError("Eleven hours is available only to the evening review")
        exit_at = min(entry + timedelta(hours=int(choice.hours or 0)), expiry - cutoff)
    else:
        if choice.exit_at is None:
            raise ValueError("Specific exit time is required")
        exit_at = choice.exit_at.astimezone(UTC)

    live = deepcopy(template)
    live["schemaVersion"] = 2
    live["expiryPolicy"] = "auto"
    live["holdingMode"] = "intraday"
    crossings = session_number(exit_at) - session_number(entry)
    live["entry"] = {
        "strategyType": "intraday",
        "entryAt": entry.isoformat(),
        "exitAt": exit_at.isoformat(),
    }
    for leg in live["legs"]:
        leg["expiry"] = expiry.astimezone(IST).date().isoformat()
    validate_session_schedule(live, trigger=trigger, review_at=review_at)
    definition = StrategyDefinition.model_validate(live).model_dump(mode="json", exclude_none=True)
    detail = {
        "entryUtc": entry.isoformat(),
        "entryIst": entry.astimezone(IST).isoformat(),
        "exitUtc": exit_at.isoformat(),
        "exitIst": exit_at.astimezone(IST).isoformat(),
        "durationMinutes": int((exit_at - entry).total_seconds() // 60),
        "sessionCrossings": crossings,
        "contractExpiryUtc": expiry.isoformat(),
        "contractExpiryIst": expiry.astimezone(IST).isoformat(),
        "latestSafeExitUtc": (expiry - cutoff).isoformat(),
        "maximumHoldHours": limit,
        "choice": choice.model_dump(mode="json", exclude_none=True),
    }
    return definition, detail
