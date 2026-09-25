"""Keep AI-selected strategy changes within the saved template's allowed fields."""

from typing import Any

from .errors import AppError

MUTABLE = {
    "entry",
    "legs",
    "holdingMode",
    "expiryPolicy",
    "acknowledgement",
    "selectionCriteria",
    "allocationMode",
    "capitalAmount",
}
ENTRY_MUTABLE = {"entryAt", "exitAt", "strategyType"}


def validate_materialized_definition(source: dict[str, Any], proposed: dict[str, Any]) -> None:
    if proposed.get("holdingMode") not in {None, "intraday", "hold_to_expiry"}:
        raise AppError(422, "Invalid holding mode", "definition_changed")
    if proposed.get("expiryPolicy") not in {None, "same_day", "next_day", "7_day", "30_day"}:
        raise AppError(422, "Invalid expiry policy", "definition_changed")
    entry = proposed.get("entry")
    original_entry = source.get("entry") or {}
    if entry is not None:
        if not isinstance(entry, dict) or not isinstance(original_entry, dict):
            raise AppError(422, "Invalid entry configuration", "definition_changed")
        if entry.get("strategyType") not in {None, "intraday", "btst", "positional"}:
            raise AppError(422, "Invalid strategy timeframe", "definition_changed")
        if any(
            original_entry.get(key) != entry.get(key)
            for key in original_entry.keys() | entry.keys()
            if key not in ENTRY_MUTABLE
        ):
            raise AppError(422, "AI proposal changed entry configuration", "definition_changed")
    if any(source.get(key) != proposed.get(key) for key in source.keys() | proposed.keys() if key not in MUTABLE):
        raise AppError(422, "AI proposal changed a strategy-owned field", "definition_changed")
    original_legs, proposed_legs = source.get("legs"), proposed.get("legs")
    if not isinstance(original_legs, list) or not isinstance(proposed_legs, list):
        raise AppError(422, "AI proposal changed strategy legs", "definition_changed")
    if len(original_legs) != len(proposed_legs):
        raise AppError(422, "AI proposal changed strategy legs", "definition_changed")
    for original, changed in zip(original_legs, proposed_legs, strict=True):
        if not isinstance(original, dict) or not isinstance(changed, dict):
            raise AppError(422, "AI proposal changed leg configuration", "definition_changed")
        if any(original.get(key) != changed.get(key) for key in original.keys() | changed.keys() if key != "expiry"):
            raise AppError(422, "AI proposal changed leg configuration", "definition_changed")
