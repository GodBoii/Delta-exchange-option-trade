"""Account migration. API secrets are encrypted in memory, never exported in plaintext."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from dotenv import load_dotenv

from app.application_data import ConvexApplicationData
from app.config import Settings
from app.credential_store import CredentialStore
from app.supabase import SupabaseAdmin
from scripts.migrate_convex_library import ROOT, digest, source_rows


async def run(action: str, path: Path, settings: Settings) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        data = ConvexApplicationData(settings.convex_url, settings.convex_trading_secret, client)
        credentials = CredentialStore(data, settings.convex_credential_key)
        if action == "export":
            db = SupabaseAdmin(settings)
            try:
                profiles = await source_rows(
                    db, "profiles", "id", "id,display_name,avatar_url,phone_number,created_at,updated_at"
                )
                connections = await source_rows(db, "exchange_connections", "id", "id,user_id,status")
                encrypted = []
                for connection in connections:
                    if connection["status"] != "connected":
                        continue
                    result = await db.rpc("get_delta_credentials", {"p_user_id": connection["user_id"]})
                    row = result[0] if isinstance(result, list) and result else result
                    if not isinstance(row, dict) or not row.get("delta_user_id"):
                        raise ValueError("Source credential identity is incomplete")
                    encrypted.append(
                        credentials.seal(
                            connection["user_id"],
                            row["api_key"],
                            row["api_secret"],
                            {
                                "id": row["delta_user_id"],
                                "connection_id": connection["id"],
                                "account_name": row["account_name"],
                                "email_masked": row["email_masked"],
                            },
                        )
                    )
            finally:
                await db.close()
            records = {"profiles": profiles, "connections": encrypted}
            with path.open("x", encoding="utf-8") as output:
                json.dump({"schemaVersion": 1, "sha256": digest(records), "records": records}, output, indent=2)
            print(f"Exported {len(profiles)} profiles and {len(encrypted)} encrypted connections")
            return
        document = json.loads(path.read_text(encoding="utf-8"))
        records = document["records"]
        if document.get("schemaVersion") != 1 or document["sha256"] != digest(records):
            raise ValueError("Account export checksum mismatch")
        if action == "import":
            for group, endpoint in (
                ("profiles", "accounts:importProfiles"),
                ("connections", "accounts:importConnections"),
            ):
                for offset in range(0, len(records[group]), 100):
                    await data.request(endpoint, {"records": records[group][offset : offset + 100]}, mutation=True)
        for source in records["profiles"]:
            target = (await data.request("accounts:overview", {"userId": source["id"]}))["profile"]
            if not target or any(target.get(key) != value for key, value in source.items()):
                raise ValueError("Profile verification mismatch")
        for source in records["connections"]:
            target = await data.request("accounts:credentials", {"userId": source["user_id"]})
            if not target or target["fingerprint"] != source["fingerprint"] or target["id"] != source["id"]:
                raise ValueError("Connection verification mismatch")
            await credentials.read(source["user_id"])
        print(f"Verified {len(records['profiles'])} profiles and {len(records['connections'])} encrypted connections")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "import", "verify"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--source-paused", action="store_true")
    args = parser.parse_args()
    if args.action == "import" and not args.source_paused:
        parser.error("Pause source account changes before import and pass --source-paused")
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / "backend/.env", override=False)
    asyncio.run(run(args.action, args.path, Settings()))


if __name__ == "__main__":
    main()
