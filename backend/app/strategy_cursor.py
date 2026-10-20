"""Stable chronological keyset pagination for the strategy history API."""

import base64
import json
from datetime import datetime
from typing import Any

from .errors import AppError


def encode_strategy_cursor(row: dict[str, Any]) -> str:
    raw = json.dumps([row["created_at"], row["id"]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_strategy_cursor(value: str) -> tuple[datetime, str]:
    try:
        items = json.loads(base64.urlsafe_b64decode(value))
        if not isinstance(items, list) or len(items) != 2 or not all(isinstance(i, str) for i in items):
            raise ValueError("Invalid cursor")
        instant = datetime.fromisoformat(items[0].replace("Z", "+00:00"))
        if instant.utcoffset() is None or not items[1] or len(items[1]) > 64:
            raise ValueError("Invalid cursor")
        return instant, items[1]
    except (ValueError, TypeError, UnicodeError) as error:
        raise AppError(422, "Invalid history cursor", "strategy_cursor_invalid") from error
