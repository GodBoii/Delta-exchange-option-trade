"""Set every current shared strategy template to a 50% take profit."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from app.application_data import ConvexApplicationData
from app.models import StrategyDefinition

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_STATUSES = {"scheduled", "executing_entry", "active", "executing_exit", "attention"}


def rows(data: ConvexApplicationData, path: str, args: dict) -> list[dict]:
    found: list[dict] = []
    cursor = None
    while True:
        page = data.request_sync(path, {**args, "paginationOpts": {"numItems": 100, "cursor": cursor}})
        found.extend(page["page"])
        if page["isDone"]:
            return found
        next_cursor = page["continueCursor"]
        if not next_cursor or next_cursor == cursor:
            raise RuntimeError(f"{path} pagination did not advance")
        cursor = next_cursor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Update the live Convex catalog")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / "backend" / ".env", override=False)
    url, secret = os.getenv("CONVEX_URL"), os.getenv("CONVEX_TRADING_SECRET")
    if not url or not secret:
        raise RuntimeError("Convex library connection is not configured")
    data = ConvexApplicationData(url, secret)
    catalog = rows(data, "library:serverList", {"userId": "global", "defaults": True})
    if not catalog:
        raise RuntimeError("The shared strategy catalog is empty")
    if args.apply:
        strategies = rows(data, "runtimeRecords:select", {
            "table": "strategies", "conditions": [], "columns": "id,status",
        })
        open_runs = [row for row in (json.loads(item) for item in strategies) if row.get("status") in ACTIVE_STATUSES]
        if open_runs:
            raise RuntimeError(f"Refusing to update TP while {len(open_runs)} strategy runs remain open")
    for row in sorted(catalog, key=lambda item: item["name"]):
        definition = json.loads(row["definitionJson"])
        if definition.get("takeProfitPercent") == 50:
            print(f"Already 50%: {row['name']} v{row['version']}")
            continue
        definition["takeProfitPercent"] = 50
        StrategyDefinition.model_validate(definition)
        if not args.apply:
            print(f"Would update: {row['name']} v{row['version']} to 50%")
            continue
        updated = data.request_sync("library:serverUpdateDefault", {
            "id": row["id"],
            "definitionJson": json.dumps(definition, separators=(",", ":")),
            "expectedVersion": row["version"],
        }, mutation=True)
        if updated["version"] != row["version"] + 1 or json.loads(updated["definitionJson"])["takeProfitPercent"] != 50:
            raise RuntimeError(f"The catalog did not confirm the 50% TP for {row['name']}")
        print(f"Updated: {row['name']} v{updated['version']} to 50%")


if __name__ == "__main__":
    main()
