"""Shared market decisions are public to signed-in users; execution remains account-owned."""

from typing import Any

SHARED_USER_ID = "global"


def enabled(settings: Any) -> bool:
    return bool(getattr(settings, "shared_analysis_enabled", True))


def history_filter(settings: Any, user_id: str) -> str:
    return f"in.({user_id},{SHARED_USER_ID})" if enabled(settings) else f"eq.{user_id}"
