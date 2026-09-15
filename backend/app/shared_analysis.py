"""Shared market decisions are public to signed-in users; execution remains account-owned."""

from typing import Any

SHARED_USER_ID = "00000000-0000-4000-8000-000000000001"


def enabled(settings: Any) -> bool:
    return bool(getattr(settings, "convex_runtime_enabled", False) and getattr(settings, "shared_analysis_enabled", True))


def history_filter(settings: Any, user_id: str) -> str:
    return f"in.({user_id},{SHARED_USER_ID})" if enabled(settings) else f"eq.{user_id}"


def choose_setup(candidates: list[dict[str, Any]], library: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the first current built-in candidate; private copies never override it."""
    catalog = {
        row["id"]: row
        for row in library
        if row.get("user_id") is None and row.get("enabled_for_ai") and not row.get("deleted")
    }
    for candidate in candidates:
        selected = catalog.get(candidate["id"])
        if selected and selected.get("version") == candidate.get("version"):
            return selected
    return None
