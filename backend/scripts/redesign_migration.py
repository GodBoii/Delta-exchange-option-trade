"""Import the two retained accounts from a verified, encrypted laptop backup.

This command never deletes source data or users. Source cleanup is a separate
step after verification and application checks.
"""

import argparse
import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from app.application_data import ConvexApplicationData, saved_row
from app.config import Settings
from app.credential_store import CredentialStore
from app.report_store import REPORT_FIELDS
from app.runtime_store import ConvexRuntimeStore
from scripts.backup_redesign import verify_archive
from scripts.migrate_convex_library import ROOT, canonical

KEEP_EMAILS = frozenset({"prajwalghadge2005@gmail.com", "yadavn519@gmail.com"})
OWNER_EMAIL = "prajwalghadge2005@gmail.com"
RUNTIME_TABLES = (
    "strategies",
    "executions",
    "execution_orders",
    "strategy_capital_slots",
    "strategy_proposals",
    "automation_agent_runs",
)


def load_backup(directory: Path) -> dict[str, Any]:
    cipher = Fernet((directory / "recovery.key").read_bytes())
    raw = cipher.decrypt((directory / "supabase.zip.fernet").read_bytes())
    verify_archive(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return json.loads(archive.read("records.json"))


def transform(document: dict[str, Any]) -> dict[str, Any]:
    tables = {name: [item["record"] for item in rows] for name, rows in document["tables"].items()}
    identities = [user for user in tables["auth.users"] if user["email"].lower() in KEEP_EMAILS]
    if {user["email"].lower() for user in identities} != KEEP_EMAILS or len(identities) != 2:
        raise ValueError("The backup must contain exactly the two requested identities")
    keep = {user["id"] for user in identities}
    owner = next(user["id"] for user in identities if user["email"].lower() == OWNER_EMAIL)
    profiles = [row for row in tables["public.profiles"] if row["id"] in keep]
    source = {table: tables["public." + table] for table in RUNTIME_TABLES}
    strategy_ids = {row["id"] for row in source["strategies"] if row["user_id"] in keep}
    execution_ids = {row["id"] for row in source["executions"] if row["strategy_id"] in strategy_ids}
    snapshots = {row["id"]: row for row in tables["public.automation_market_snapshots"]}
    runtime = {}
    reports = []
    for table, rows in source.items():
        retained = []
        for original in rows:
            row = dict(original)
            if table == "executions":
                eligible = row["strategy_id"] in strategy_ids
            elif table == "execution_orders":
                eligible = row["execution_id"] in execution_ids
            else:
                eligible = row["user_id"] in keep
            if not eligible:
                continue
            if table == "automation_agent_runs":
                snapshot = snapshots.get(row.get("market_snapshot_id"), {})
                market = dict(snapshot.get("market_json") or {})
                market.pop("chartImages", None)
                reports.append(
                    {
                        "id": row["id"],
                        "snapshot_id": row.get("market_snapshot_id"),
                        "legacy_user_id": row["user_id"],
                        "created_at": row["created_at"],
                        "updated_at": row.get("updated_at", row["created_at"]),
                        "report_markdown": row.get("report_markdown"),
                        "member_responses": row.get("member_responses") or [],
                        "tool_calls": row.get("tool_calls") or [],
                        "market_json": market,
                        "account_json": snapshot.get("account_json") or {},
                    }
                )
                row = {key: value for key, value in row.items() if key not in REPORT_FIELDS}
                if row["status"] == "running":
                    raise ValueError("Cannot migrate a running analysis")
            if table == "strategies" and row["status"] not in {"completed", "cancelled"}:
                raise ValueError("Resolve live or scheduled trading exposure before cutover")
            if table in {"automation_agent_runs", "strategy_proposals"} and row["status"] == "scheduled":
                row["status"] = "cancelled"
                row["error" if table == "automation_agent_runs" else "rejection_reason"] = (
                    "Retired during migration to the global analysis schedule"
                )
            retained.append(row)
        runtime[table] = retained
    return {
        "owner": owner,
        "identities": identities,
        "profiles": profiles,
        "runtime": runtime,
        "reports": reports,
        "capital": {row["user_id"]: row for row in tables["public.capital_settings"] if row["user_id"] in keep},
        "automation": {row["user_id"]: row for row in tables["public.automation_settings"] if row["user_id"] in keep},
        "credentials": {key: value for key, value in document["credentials"].items() if key in keep},
        "library": [
            row for row in tables["public.saved_strategies"] if row["user_id"] is None or row["user_id"] in keep
        ],
    }


async def run(action: str, directory: Path, settings: Settings) -> None:
    expected = transform(load_backup(directory))
    counts = {table: len(rows) for table, rows in expected["runtime"].items()}
    if action == "plan":
        print(
            json.dumps(
                {
                    "owner": expected["owner"],
                    "users": len(expected["profiles"]),
                    "library": len(expected["library"]),
                    "reports": len(expected["reports"]),
                    "runtime": counts,
                }
            )
        )
        return
    async with httpx.AsyncClient(timeout=60) as client:
        data = ConvexApplicationData(settings.convex_url, settings.convex_trading_secret, client)
        credentials = CredentialStore(data, settings.convex_credential_key)
        users = []
        for profile in expected["profiles"]:
            user_id = profile["id"]
            capital = expected["capital"][user_id]
            automation = expected["automation"].get(user_id, {})
            source = expected["credentials"].get(user_id, [])
            connection = None
            if source:
                value = source[0]["record"]
                if value.get("status") == "connected":
                    connection = credentials.seal(
                        user_id,
                        value["api_key"],
                        value["api_secret"],
                        {
                            "id": value["delta_user_id"],
                            "account_name": value["account_name"],
                            "email_masked": value["email_masked"],
                            "connection_id": value["connection_id"],
                        },
                    )
            users.append(
                {
                    "userId": user_id,
                    "capital": {
                        "allocation_mode": capital["allocation_mode"],
                        "capital_amount": str(capital["capital_amount"])
                        if capital.get("capital_amount") is not None
                        else None,
                    },
                    "automation": {
                        "enabled": automation.get("enabled", False),
                        "model_id": "xiaomi/mimo-v2.6-pro",
                        "minimum_follow_up_minutes": automation.get("minimum_follow_up_minutes", 5),
                        "maximum_agent_runs_per_day": automation.get("maximum_agent_runs_per_day", 3),
                    },
                    "connection": connection,
                    "createdAt": profile["created_at"],
                    "updatedAt": profile["updated_at"],
                }
            )
        headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Prefer": "resolution=merge-duplicates",
        }
        if action == "import":
            # Preserve existing ciphertext on a retry; verify its identity below.
            current_users = await data.request("accounts:serverUsers", {})
            by_id = {user["userId"]: user for user in current_users}
            for user in users:
                current = by_id.get(user["userId"])
                if current and current["connection"] and user["connection"]:
                    if current["connection"]["fingerprint"] != user["connection"]["fingerprint"]:
                        raise ValueError("Credential import conflicts")
                    user["connection"] = current["connection"]
            await data.request(
                "accounts:importUsers", {"records": users, "ownerUserId": expected["owner"]}, mutation=True
            )
            for row in expected["library"]:
                current = await data.request(
                    "library:serverGet", {"userId": row["user_id"] or "migration", "id": row["id"]}
                )
                record = {
                    key: row[key]
                    for key in (
                        "id",
                        "user_id",
                        "name",
                        "source_run_id",
                        "version",
                        "enabled_for_ai",
                        "created_at",
                        "updated_at",
                    )
                }
                record["definitionJson"] = canonical(row["definition_json"])
                await data.request(
                    "migration:replaceCatalog",
                    {
                        "record": record,
                        "expectedVersion": current["version"] if current else None,
                        "expectedDefinition": current["definitionJson"] if current else None,
                    },
                    mutation=True,
                )
            for start in range(0, len(expected["reports"]), 5):
                response = await client.post(
                    settings.supabase_url + "/rest/v1/analysis_reports",
                    headers=headers,
                    params={"on_conflict": "id"},
                    json=expected["reports"][start : start + 5],
                )
                response.raise_for_status()
            for table, rows in expected["runtime"].items():
                for start in range(0, len(rows), 25):
                    await data.request(
                        "runtimeRecords:importBatch",
                        {
                            "table": "analysisJobs" if table == "automation_agent_runs" else table,
                            "rows": [canonical(row) for row in rows[start : start + 25]],
                        },
                        mutation=True,
                    )
        actual_users = await data.request("accounts:serverUsers", {})
        if {row["userId"] for row in actual_users} != {row["userId"] for row in users}:
            raise ValueError("Unexpected Convex account set")
        for source in users:
            target = next(row for row in actual_users if row["userId"] == source["userId"])
            for field in ("capital", "automation", "createdAt", "updatedAt"):
                if target[field] != source[field]:
                    raise ValueError(f"Account mismatch: {field}")
            if source["connection"]:
                restored = await credentials.read(source["userId"])
                original = expected["credentials"][source["userId"]][0]["record"]
                if any(restored[key] != str(original[key]) for key in ("api_key", "api_secret", "delta_user_id")):
                    raise ValueError("Credential restoration mismatch")
        for row in expected["library"]:
            actual = saved_row(
                await data.request("library:serverGet", {"userId": row["user_id"] or "migration", "id": row["id"]})
            )
            if any(actual.get(key) != value for key, value in row.items() if key in actual):
                raise ValueError(f"Library mismatch: {row['id']}")
        store = ConvexRuntimeStore(data)
        for table, rows in expected["runtime"].items():
            actual = await store.select(table, {"select": "*", "order": "id.asc"})
            if actual != sorted(rows, key=lambda row: row["id"]):
                raise ValueError(f"Runtime mismatch: {table}")
        actual_reports = []
        cursor = None
        while True:
            params = {"select": "*", "order": "id.asc", "limit": "5"}
            if cursor:
                params["id"] = f"gt.{cursor}"
            response = await client.get(
                settings.supabase_url + "/rest/v1/analysis_reports", headers=headers, params=params
            )
            response.raise_for_status()
            page = response.json()
            actual_reports.extend(page)
            if len(page) < 5:
                break
            cursor = page[-1]["id"]
        # PostgreSQL normalizes timestamp formatting; compare instants separately.
        from datetime import datetime

        by_id = {row["id"]: row for row in actual_reports}
        if set(by_id) != {row["id"] for row in expected["reports"]}:
            raise ValueError("Report count/identity mismatch")
        for row in expected["reports"]:
            for key, value in row.items():
                current = by_id[row["id"]][key]
                if key in {"created_at", "updated_at"}:
                    if datetime.fromisoformat(current) != datetime.fromisoformat(value):
                        raise ValueError("Report timestamp mismatch")
                elif current != value:
                    raise ValueError(f"Report mismatch: {key}")
        evidence = {"verified": True, "users": 2, "reports": len(actual_reports), "runtime": counts}
        (directory / "migration-verification.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "import", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-paused", action="store_true")
    args = parser.parse_args()
    if args.action == "import" and not args.source_paused:
        parser.error("Stop source application writers and pass --source-paused")
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / "backend/.env", override=False)
    asyncio.run(run(args.action, args.directory.resolve(), Settings()))


if __name__ == "__main__":
    sys.exit(main())
