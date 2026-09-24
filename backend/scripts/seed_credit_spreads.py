"""Add the two bounded-risk BTC credit spreads to the active Convex library."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from app.application_data import ConvexApplicationData
from app.default_strategies import default_strategy_definitions

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IDS = {
    "Bull put credit spread": "10000000-0000-4000-8000-000000000014",
    "Bear call credit spread": "10000000-0000-4000-8000-000000000015",
}


def new_defaults(now: datetime) -> dict[str, dict]:
    return {
        item.name: item.model_dump(mode="json", exclude_none=True)
        for item in default_strategy_definitions(now)
        if item.name in DEFAULT_IDS
    }


def shared_rows(data: ConvexApplicationData) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    while True:
        page = data.request_sync(
            "library:serverList",
            {"userId": "global", "defaults": True, "paginationOpts": {"numItems": 100, "cursor": cursor}},
        )
        rows.extend(page["page"])
        if page["isDone"]:
            return rows
        next_cursor = page["continueCursor"]
        if not next_cursor or next_cursor == cursor:
            raise RuntimeError("Shared library pagination did not advance")
        cursor = next_cursor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create missing shared templates")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / "backend" / ".env", override=False)
    url, secret = os.getenv("CONVEX_URL"), os.getenv("CONVEX_TRADING_SECRET")
    if not url or not secret:
        raise RuntimeError("Convex library connection is not configured")
    data = ConvexApplicationData(url, secret)
    existing = {item["name"]: item for item in shared_rows(data) if item["user_id"] is None}
    expected = new_defaults(datetime.now(UTC))
    if set(expected) != set(DEFAULT_IDS):
        raise RuntimeError("The expected credit spread definitions are missing")
    for name, identity in DEFAULT_IDS.items():
        row = existing.get(name)
        if row:
            if row["id"] != identity or not row["enabled_for_ai"]:
                raise RuntimeError(f"Shared strategy conflicts with {name}")
            print(f"Existing: {name}, version {row['version']}")
            continue
        if not args.apply:
            print(f"Would create: {name}")
            continue
        definition = expected[name]
        created = data.request_sync(
            "library:serverCreateDefault",
            {"id": identity, "definitionJson": json.dumps(definition, separators=(",", ":"))},
            mutation=True,
        )
        if created["id"] != identity or created["name"] != name or not created["enabled_for_ai"]:
            raise RuntimeError(f"The library did not confirm {name}")
        print(f"Created: {name}, version {created['version']}")


if __name__ == "__main__":
    main()
