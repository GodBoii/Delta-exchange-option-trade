"""Restore the Convex recovery copy into an empty database and lock trading."""

import argparse
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg import sql
from psycopg.types.json import Jsonb

from scripts.import_convex_export import documents

TABLES = {
    "users",
    "system_settings",
    "saved_strategies",
    "strategies",
    "executions",
    "execution_orders",
    "strategy_capital_slots",
    "strategy_proposals",
    "analysis_jobs",
    "order_intents",
    "product_claims",
    "exchange_fills",
}


def validate_credential(payload: dict[str, Any], key: str | None) -> None:
    if payload.get("user_id") is None:
        raise ValueError("Recovery account is missing its user identity")
    connection = payload.get("connection")
    if not connection or connection.get("status") != "connected":
        return
    if not key or not connection.get("ciphertext"):
        raise ValueError("A connected account requires its credential recovery key")
    try:
        decoded = json.loads(Fernet(key.encode()).decrypt(connection["ciphertext"].encode()))
    except (InvalidToken, ValueError, TypeError) as error:
        raise ValueError("Recovery credential cannot be decrypted") from error
    if decoded.get("user_id") != payload["user_id"] or decoded.get("delta_user_id") != connection.get("delta_user_id"):
        raise ValueError("Recovery credential identity mismatch")


def restore(path: Path, database_url: str, credential_key: str | None) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        records = documents(archive, "recoveryRecords")
        manifests = documents(archive, "recoveryManifests")
    if not manifests:
        raise ValueError("Recovery export has no daily manifest")
    manifest = max(manifests, key=lambda item: item["day"])
    if not isinstance(manifest.get("lastOutboxId"), int) or not manifest.get("checksum"):
        raise ValueError("Recovery manifest is invalid")
    try:
        manifest_counts = json.loads(manifest["countsJson"])
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("Recovery manifest counts are invalid") from error
    if not isinstance(manifest_counts, dict):
        raise ValueError("Recovery manifest counts are invalid")
    by_type: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    seen: set[tuple[str, str]] = set()
    for record in records:
        table = record.get("entityType")
        key = record.get("entityKey")
        if table not in TABLES or not isinstance(key, str) or (table, key) in seen:
            raise ValueError("Invalid or duplicate recovery record")
        seen.add((table, key))
        if record.get("payloadJson") is None:
            continue
        payload = json.loads(record["payloadJson"])
        if not isinstance(payload, dict) or not isinstance(record.get("revision"), int):
            raise ValueError("Invalid recovery payload")
        if table == "users":
            validate_credential(payload, credential_key)
        if table == "order_intents":
            payload.pop("strategy_id", None)
        payload["revision"] = record["revision"]
        by_type[table].append(payload)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("select pg_advisory_xact_lock(hashtext('trade-local-import'))")
        connection.execute("select set_config('trade.skip_mirror','on',true)")
        for table in TABLES:
            count = connection.execute(
                sql.SQL("select count(*) from {}").format(sql.Identifier("trade", table))
            ).fetchone()[0]
            if count:
                raise ValueError(f"Recovery destination is not empty: {table}")
        for table in sorted(TABLES):
            for payload in by_type[table]:
                columns = list(payload)
                values = [
                    Jsonb(payload[key]) if isinstance(payload[key], (dict, list)) else payload[key] for key in columns
                ]
                statement = sql.SQL("insert into {} ({}) values ({})").format(
                    sql.Identifier("trade", table),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns),
                )
                connection.execute(statement, values)
            count = connection.execute(
                sql.SQL("select count(*) from {}").format(sql.Identifier("trade", table))
            ).fetchone()[0]
            if count != len(by_type[table]):
                raise ValueError(f"Recovery count mismatch: {table}")
        connection.execute(
            """update trade.recovery_gate set pending=true,
               reason='Convex recovery restored; verify manifest and Delta exposure before resuming',
               updated_at=now() where key='main'"""
        )
    restored = {table: len(rows) for table, rows in sorted(by_type.items())}
    differences = {table: {"manifest": manifest_counts.get(table), "restored": count}
                   for table, count in restored.items() if table in manifest_counts and manifest_counts[table] != count}
    return {
        "restored": restored,
        "manifestDay": manifest["day"],
        "lastOutboxId": manifest["lastOutboxId"],
        "manifestChecksum": manifest["checksum"],
        "countDifferences": differences,
        "tradingPaused": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source-confirmed", action="store_true")
    args = parser.parse_args()
    if not args.apply or not args.source_confirmed:
        parser.error("Restoring requires --apply and --source-confirmed; it keeps trading paused")
    url = os.getenv("LOCAL_DATABASE_URL")
    if not url:
        parser.error("LOCAL_DATABASE_URL is required")
    print(json.dumps(restore(args.export, url, os.getenv("CONVEX_CREDENTIAL_KEY")), sort_keys=True))


if __name__ == "__main__":
    main()
