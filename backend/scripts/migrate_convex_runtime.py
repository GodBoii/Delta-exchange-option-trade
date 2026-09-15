"""Export/import operational records. Does not run SQL or stop production."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from dotenv import load_dotenv

from app.application_data import ConvexApplicationData
from app.config import Settings
from app.runtime_store import ConvexRuntimeStore
from app.supabase import SupabaseAdmin
from scripts.migrate_convex_library import ROOT, canonical, digest, source_rows

ORDER = (
    "automation_settings",
    "strategies",
    "executions",
    "execution_orders",
    "strategy_capital_slots",
    "automation_market_snapshots",
    "strategy_proposals",
    "automation_agent_runs",
)


async def run(action: str, path: Path, settings: Settings) -> None:
    if action == "export":
        db = SupabaseAdmin(settings.model_copy(update={"convex_runtime_enabled": False}))
        try:
            records = {
                table: await source_rows(db, table, "user_id" if table == "automation_settings" else "id", "*")
                for table in ORDER
            }
        finally:
            await db.close()
        for row in records["automation_settings"]:
            row["id"] = row["user_id"]
        document = {"schemaVersion": 1, "records": records, "sha256": digest(records)}
        with path.open("x", encoding="utf-8") as output:
            json.dump(document, output, indent=2, allow_nan=False)
        print({table: len(rows) for table, rows in records.items()})
        return
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document["records"]
    if document.get("schemaVersion") != 1 or digest(records) != document["sha256"]:
        raise ValueError("Runtime export checksum mismatch")
    async with httpx.AsyncClient(timeout=30) as client:
        data = ConvexApplicationData(settings.convex_url, settings.convex_trading_secret, client)
        if action == "import":
            for table in ORDER:
                for row in records[table]:
                    await data.request(
                        "runtimeRecords:write",
                        {"table": table, "rowJson": canonical(row), "importOnly": True},
                        mutation=True,
                    )
        store = ConvexRuntimeStore(data)
        for table in ORDER:
            actual = await store.select(table, {"select": "*", "order": "id.asc"})
            expected = sorted(records[table], key=lambda row: row["id"])
            if canonical(actual) != canonical(expected):
                raise ValueError(f"Runtime verification failed for {table}")
        print({"verified": True, "counts": {table: len(rows) for table, rows in records.items()}})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "import", "verify"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--source-paused", action="store_true")
    args = parser.parse_args()
    if args.action == "import" and not args.source_paused:
        parser.error("Runtime writers must be stopped before import; then pass --source-paused")
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / "backend/.env", override=False)
    asyncio.run(run(args.action, args.path, Settings()))


if __name__ == "__main__":
    main()
