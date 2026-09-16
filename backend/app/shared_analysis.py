"""Shared market decisions are public to signed-in users; execution remains account-owned."""

from typing import Any

SHARED_USER_ID = "00000000-0000-4000-8000-000000000001"


def enabled(settings: Any) -> bool:
    return bool(
        getattr(settings, "convex_runtime_enabled", False) and getattr(settings, "shared_analysis_enabled", True)
    )


def history_filter(settings: Any, user_id: str) -> str:
    return f"in.({user_id},{SHARED_USER_ID})" if enabled(settings) else f"eq.{user_id}"
