"""Export, import, or verify the library cutover without applying SQL or deleting data."""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from app.application_data import ConvexApplicationData
from app.config import Settings
from app.supabase import SupabaseAdmin

ROOT = Path(__file__).resolve().parents[2]


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


async def source_rows(db: SupabaseAdmin, table: str, key: str, columns: str) -> list[dict[str, Any]]:
    result = []
    params = {"select": columns, "order": f"{key}.asc", "limit": "100"}
    previous = None
    while True:
        rows = await db.select(table, params)
        result.extend(rows)
        if len(rows) < 100:
            return result
        cursor = str(rows[-1][key])
        if previous is not None and cursor <= previous:
            raise RuntimeError("Source pagination did not advance")
        params[key] = f"gt.{cursor}"
        previous = cursor


async def export_source(settings: Settings, path: Path) -> None:
    db = SupabaseAdmin(settings)
    try:
        strategies, capital = await asyncio.gather(
            source_rows(
                db,
                "saved_strategies",
                "id",
                "id,user_id,name,definition_json,source_run_id,version,enabled_for_ai,created_at,updated_at",
            ),
            source_rows(db, "capital_settings", "user_id", "user_id,allocation_mode,capital_amount"),
        )
    finally:
        await db.close()
    records = {
        "strategies": [
            {
                **{key: value for key, value in row.items() if key != "definition_json"},
                "definitionJson": canonical(row["definition_json"]),
            }
            for row in strategies
        ],
        "capital": [
            {**row, "capital_amount": str(row["capital_amount"]) if row["capital_amount"] is not None else None}
            for row in capital
        ],
    }
    document = {
        "schemaVersion": 1,
        "exportedAt": datetime.now(UTC).isoformat(),
        "sha256": digest(records),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(document, output, indent=2, allow_nan=False)
    print(f"Exported {len(strategies)} strategies and {len(capital)} capital settings to {path}")


async def migrate(action: str, path: Path, settings: Settings) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or digest(document["records"]) != document.get("sha256"):
        raise ValueError("Export checksum or schema does not match")
    records = document["records"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
        store = ConvexApplicationData(settings.convex_url, settings.convex_trading_secret, client)
        if action == "import":
            for key, endpoint in (("strategies", "library:importStrategies"), ("capital", "library:importCapital")):
                for offset in range(0, len(records[key]), 100):
                    await store.request(endpoint, {"records": records[key][offset : offset + 100]}, mutation=True)
        for record in records["strategies"]:
            value = await store.request(
                "library:serverGet", {"id": record["id"], "userId": record["user_id"] or "migration-verification"}
            )
            if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in record.items()):
                raise RuntimeError(f"Strategy verification failed for {record['id']}")
        for record in records["capital"]:
            value = await store.request("library:getCapital", {"userId": record["user_id"]})
            if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in record.items()):
                raise RuntimeError("Capital verification failed")
    print(
        f"Verified {len(records['strategies'])} strategies and {len(records['capital'])} capital settings; "
        "source tables unchanged"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "import", "verify"))
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--source-paused", action="store_true", help="Confirm source library/settings writes are paused for import"
    )
    args = parser.parse_args()
    if args.action == "import" and not args.source_paused:
        parser.error(
            "Import requires --source-paused after pausing source writes; the script does not pause production"
        )
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / "backend/.env", override=False)
    settings = Settings()
    asyncio.run(
        export_source(settings, args.path) if args.action == "export" else migrate(args.action, args.path, settings)
    )


if __name__ == "__main__":
    main()
