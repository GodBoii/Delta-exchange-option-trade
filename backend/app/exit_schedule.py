"""Resolve an exit decision and a listed option expiry before a trade is scheduled."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator

from .models import StrategyDefinition

IST = ZoneInfo("Asia/Kolkata")
SESSION_END = time(17, 30)
PRESETS: dict[str, frozenset[int]] = {
    "intraday": frozenset({7, 11}),
    "overnight": frozenset({16, 24}),
    "positional": frozenset({48, 72}),
}
MIN_EXPIRY_HOLD = timedelta(minutes=90)


class ExitChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["intraday", "overnight", "positional", "specific_time", "expiry"]
    hours: int | None = None
    exit_at: datetime | None = None
    expiry_number: Literal[1, 2] | None = None

    @model_validator(mode="after")
    def validate_choice(self) -> "ExitChoice":
        if self.kind in PRESETS:
            if self.hours not in PRESETS[self.kind] or self.exit_at is not None or self.expiry_number is not None:
                raise ValueError(f"{self.kind} requires one of {sorted(PRESETS[self.kind])} hours only")
        elif self.kind == "specific_time":
            if self.exit_at is None or self.exit_at.utcoffset() is None or self.hours is not None or self.expiry_number is not None:
                raise ValueError("specific_time requires a timezone-aware exit_at only")
        elif self.expiry_number not in (1, 2) or self.hours is not None or self.exit_at is not None:
            raise ValueError("expiry requires expiry_number 1 or 2 only")
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
    template: dict[str, Any], *, entry_at: datetime, choice: ExitChoice, options: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an immutable live v2 definition and an explanation of its schedule."""
    if entry_at.utcoffset() is None:
        raise ValueError("Entry time must include a timezone")
    entry = entry_at.astimezone(UTC)
    cutoff = timedelta(minutes=int(template.get("exitMinutesBeforeExpiry") or 5))
    expiries = _listed_expiries(template, options)
    if not expiries:
        raise ValueError("No listed expiry supports every leg and strike rule")

    if choice.kind == "expiry":
        eligible = [expiry for expiry in expiries if expiry - cutoff - entry >= MIN_EXPIRY_HOLD]
        if len(eligible) < int(choice.expiry_number or 0):
            raise ValueError("The requested listed expiry is unavailable after the 90-minute minimum and buffer")
        expiry = eligible[int(choice.expiry_number or 1) - 1]
        exit_at = expiry - cutoff
    else:
        exit_at = (
            choice.exit_at.astimezone(UTC)
            if choice.kind == "specific_time" and choice.exit_at is not None
            else entry + timedelta(hours=int(choice.hours or 0))
        )
        if exit_at <= entry:
            raise ValueError("Exit time must be after entry")
        crossings = session_number(exit_at) - session_number(entry)
        required = {"intraday": 0, "overnight": 1}
        if choice.kind in required and crossings != required[choice.kind]:
            raise ValueError(f"{choice.kind} must cross {required[choice.kind]} options-session boundaries")
        if choice.kind == "positional" and crossings < 2:
            raise ValueError("positional must cross at least two options-session boundaries")
        expiry = next((item for item in expiries if item - cutoff >= exit_at), None)
        if expiry is None:
            raise ValueError("No listed option expiry covers the full requested hold and safety buffer")

    live = deepcopy(template)
    live["schemaVersion"] = 2
    live["expiryPolicy"] = "auto"
    live["holdingMode"] = "hold_to_expiry" if choice.kind == "expiry" else "intraday"
    crossings = session_number(exit_at) - session_number(entry)
    live["entry"] = {
        "strategyType": "intraday" if crossings == 0 else "btst" if crossings == 1 else "positional",
        "entryAt": entry.isoformat(), "exitAt": exit_at.isoformat(),
    }
    for leg in live["legs"]:
        leg["expiry"] = expiry.astimezone(IST).date().isoformat()
    definition = StrategyDefinition.model_validate(live).model_dump(mode="json", exclude_none=True)
    detail = {
        "entryUtc": entry.isoformat(), "entryIst": entry.astimezone(IST).isoformat(),
        "exitUtc": exit_at.isoformat(), "exitIst": exit_at.astimezone(IST).isoformat(),
        "durationMinutes": int((exit_at - entry).total_seconds() // 60),
        "sessionCrossings": crossings,
        "contractExpiryUtc": expiry.isoformat(), "contractExpiryIst": expiry.astimezone(IST).isoformat(),
        "latestSafeExitUtc": (expiry - cutoff).isoformat(),
        "choice": choice.model_dump(mode="json", exclude_none=True),
    }
    return definition, detail
