"""Review or update descriptions of existing shared strategies in Convex."""

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


def descriptions(now: datetime) -> dict[str, str]:
    return {item.name: item.description for item in default_strategy_definitions(now)}


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
    parser.add_argument("--apply", action="store_true", help="Save description-only changes")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / "backend" / ".env", override=False)
    url, secret = os.getenv("CONVEX_URL"), os.getenv("CONVEX_TRADING_SECRET")
    if not url or not secret:
        raise RuntimeError("Convex library connection is not configured")
    data = ConvexApplicationData(url, secret)
    expected = descriptions(datetime.now(UTC))
    rows = [row for row in shared_rows(data) if row["user_id"] is None]
    unknown = sorted(row["name"] for row in rows if row["name"] not in expected)
    if unknown:
        raise RuntimeError(f"Shared strategies have no canonical description: {', '.join(unknown)}")
    changed = 0
    for row in rows:
        definition = json.loads(row["definitionJson"])
        description = expected[row["name"]]
        if definition.get("description") == description:
            continue
        definition["description"] = description
        changed += 1
        print(f"{'Updating' if args.apply else 'Would update'} {row['name']} (version {row['version']})")
        if args.apply:
            data.request_sync(
                "library:serverUpdateDefault",
                {
                    "id": row["id"],
                    "definitionJson": json.dumps(definition, separators=(",", ":")),
                    "expectedVersion": row["version"],
                },
                mutation=True,
            )
    state = "updated" if args.apply else "need updates"
    print(f"{changed} descriptions {state}; {len(rows)} shared strategies checked")


if __name__ == "__main__":
    main()
