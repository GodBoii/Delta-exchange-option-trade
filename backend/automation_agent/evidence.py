"""Serialize agent evidence and filter option history to assigned expiries."""

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.automation_schedule import IST


def dumps(value: Any) -> str:
    def display(item):
        if isinstance(item, float):
            return float(f"{item:.8g}")
        if isinstance(item, dict):
            return {key: display(value) for key, value in item.items()}
        if isinstance(item, list):
            return [display(value) for value in item]
        return item

    return json.dumps(display(value), separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def assigned_option_overview(packet: dict, expiry_dates: list[str]) -> list[dict]:
    """Keep recheck overview history tied to the assigned legs, including previously scheduled expiries."""
    rows = []
    for row in (packet.get("enrichment") or {}).get("options", []):
        try:
            expiry = datetime.fromisoformat(str(row.get("expiry", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if expiry.utcoffset() is not None and expiry.astimezone(IST).date().isoformat() in expiry_dates:
            rows.append(deepcopy(row))
    return rows
