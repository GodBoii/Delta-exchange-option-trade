"""Load a stopped Trade Cognition Convex export into an empty local database."""

import argparse
import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

RUNTIME_TABLES = {
    "strategies": "strategies",
    "executions": "executions",
    "execution_orders": "execution_orders",
    "strategy_capital_slots": "strategy_capital_slots",
    "strategy_proposals": "strategy_proposals",
    "analysisJobs": "analysis_jobs",
}
TABLES = (
    "users",
    "systemSettings",
    "savedStrategies",
    *RUNTIME_TABLES,
    "orderIntents",
    "exchangeFills",
    "productClaims",
)
TARGET_TABLES = {
    **RUNTIME_TABLES,
    "systemSettings": "system_settings",
    "savedStrategies": "saved_strategies",
    "orderIntents": "order_intents",
    "exchangeFills": "exchange_fills",
    "productClaims": "product_claims",
}


def target_table(table: str) -> str:
    return TARGET_TABLES.get(table, table)


def documents(archive: zipfile.ZipFile, table: str) -> list[dict[str, Any]]:
    name = f"{table}/documents.jsonl"
    if name not in archive.namelist():
        return []
    rows = []
    with archive.open(name) as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Invalid {table} document")
                rows.append(value)
    return rows


def checksum(rows: list[dict[str, Any]]) -> str:
    normalized = [json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for row in rows]
    return hashlib.sha256("\n".join(sorted(normalized)).encode()).hexdigest()


def iso_from_ms(value: int | float) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def runtime_row(document: dict[str, Any]) -> tuple[Any, ...]:
    row = json.loads(document["rowJson"])
    if not isinstance(row, dict):
        raise ValueError("Runtime row is not an object")
    if row.get("scope") == "global":
        row["user_id"] = "global"
    external_id = document["externalId"]
    row["id"] = external_id
    return (
        external_id,
        document["owner"],
        document["status"],
        document.get("relation") or None,
        document.get("uniqueKey") or None,
        iso_from_ms(document["created"] or document["_creationTime"]),
        row.get("entry_at"),
        row.get("exit_at"),
        row.get("scheduled_for"),
        row.get("activation_time"),
        row.get("started_at"),
        Jsonb(row),
    )


def import_rows(connection: psycopg.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if table in RUNTIME_TABLES:
        target = sql.Identifier("trade", RUNTIME_TABLES[table])
        statement = sql.SQL(
            """insert into {} (id,owner_id,status,relation_id,unique_key,created_at,
               entry_at,exit_at,scheduled_for,activation_time,started_at,data)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
        ).format(target)
        for row in rows:
            connection.execute(statement, runtime_row(row))
        return
    if table == "users":
        for row in rows:
            value = {key: item for key, item in row.items() if not key.startswith("_")}
            connection.execute(
                """insert into trade.users (user_id,connection,automation,capital,record)
                   values (%s,%s,%s,%s,%s)""",
                (
                    row["userId"],
                    Jsonb(row.get("connection")),
                    Jsonb(row["automation"]),
                    Jsonb(row["capital"]),
                    Jsonb(value),
                ),
            )
        return
    if table == "systemSettings":
        for row in rows:
            connection.execute(
                """insert into trade.system_settings
                   (key,owner_user_id,outbound_ip,ip_checked_at,analysis) values (%s,%s,%s,%s,%s)""",
                (row["key"], row["ownerUserId"], row.get("outboundIp"), row.get("ipCheckedAt"), Jsonb(row["analysis"])),
            )
        return
    if table == "savedStrategies":
        for row in rows:
            connection.execute(
                """insert into trade.saved_strategies
                   (id,user_id,name,definition_json,enabled_for_ai,version,source_run_id,deleted,created_at,updated_at)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    row["id"],
                    row["user_id"],
                    row["name"],
                    Jsonb(json.loads(row["definitionJson"])),
                    row["enabled_for_ai"],
                    row["version"],
                    row.get("source_run_id"),
                    row["deleted"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        return
    if table == "orderIntents":
        for row in rows:
            connection.execute(
                """insert into trade.order_intents
                   (account_id,client_order_id,payload,context,materialized,outcome,unresolved,created_at,updated_at)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    row["accountId"],
                    row["clientOrderId"],
                    row["payload"],
                    Jsonb(row.get("context")),
                    row.get("materialized", False),
                    Jsonb(row["outcome"]),
                    row["unresolved"],
                    iso_from_ms(row["_creationTime"]),
                    iso_from_ms(row["updatedAt"]),
                ),
            )
        return
    if table == "exchangeFills":
        for row in rows:
            connection.execute(
                """insert into trade.exchange_fills
                   (account_id,fill_id,product_id,order_id,side,quantity,price,commission,occurred_at)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    row["accountId"],
                    row["fillId"],
                    row["productId"],
                    row["orderId"],
                    row["side"],
                    row["quantity"],
                    row["price"],
                    row.get("commission"),
                    row["occurredAt"],
                ),
            )
        return
    if table == "productClaims":
        for row in rows:
            connection.execute(
                """insert into trade.product_claims (account_id,product_id,strategy_id) values (%s,%s,%s)""",
                (row["accountId"], row["productId"], row["strategyId"]),
            )
        return
    raise ValueError(f"Unexpected table: {table}")


def verify_rows(connection: psycopg.Connection, table: str, source: list[dict[str, Any]]) -> None:
    target = sql.Identifier("trade", target_table(table))
    if table in RUNTIME_TABLES:
        actual = {
            row["id"]: row
            for row in connection.execute(
                sql.SQL("select id,owner_id,status,relation_id,unique_key,created_at,data from {}").format(target)
            ).fetchall()
        }
        for document in source:
            identity = document["externalId"]
            record = actual.get(identity)
            expected = json.loads(document["rowJson"])
            if expected.get("scope") == "global":
                expected["user_id"] = "global"
            expected["id"] = identity
            if not record or record["data"] != expected or record["owner_id"] != document["owner"]:
                raise ValueError(f"Runtime import mismatch: {table}/{identity}")
            if record["status"] != document["status"] or record["relation_id"] != (document.get("relation") or None):
                raise ValueError(f"Runtime index mismatch: {table}/{identity}")
            if record["unique_key"] != (document.get("uniqueKey") or None):
                raise ValueError(f"Runtime unique-key mismatch: {table}/{identity}")
            if record["created_at"] != datetime.fromisoformat(
                iso_from_ms(document["created"] or document["_creationTime"])
            ):
                raise ValueError(f"Runtime creation time mismatch: {table}/{identity}")
        return
    if table == "users":
        actual = {
            row["user_id"]: row["record"]
            for row in connection.execute("select user_id,record from trade.users").fetchall()
        }
        for document in source:
            value = {key: item for key, item in document.items() if not key.startswith("_")}
            if actual.get(document["userId"]) != value:
                raise ValueError(f"Account import mismatch: {document['userId']}")
            encrypted = document.get("connection")
            if encrypted and encrypted.get("status") == "connected":
                key = os.getenv("CONVEX_CREDENTIAL_KEY")
                if not key or not encrypted.get("ciphertext"):
                    raise ValueError("Connected account requires its credential recovery key")
                try:
                    decoded = json.loads(Fernet(key.encode()).decrypt(encrypted["ciphertext"].encode()))
                except (InvalidToken, ValueError, TypeError) as error:
                    raise ValueError("Imported credential cannot be decrypted") from error
                if (decoded.get("user_id") != document["userId"]
                        or decoded.get("delta_user_id") != encrypted.get("delta_user_id")
                        or not decoded.get("api_key") or not decoded.get("api_secret")):
                    raise ValueError("Imported credential identity mismatch")
        return
    if table == "systemSettings":
        actual = {
            row["key"]: row
            for row in connection.execute(
                "select key,owner_user_id,host(outbound_ip) as outbound_ip,"
                "ip_checked_at,analysis from trade.system_settings"
            ).fetchall()
        }
        for document in source:
            row = actual.get(document["key"])
            expected_time = (
                datetime.fromisoformat(document["ipCheckedAt"].replace("Z", "+00:00"))
                if document.get("ipCheckedAt")
                else None
            )
            if not row or any(
                (
                    row["owner_user_id"] != document["ownerUserId"],
                    row["outbound_ip"] != document.get("outboundIp"),
                    row["ip_checked_at"] != expected_time,
                    row["analysis"] != document["analysis"],
                )
            ):
                raise ValueError("System settings import mismatch")
        return
    if table == "savedStrategies":
        actual = {str(row["id"]): row for row in connection.execute("select * from trade.saved_strategies").fetchall()}
        for document in source:
            row = actual.get(document["id"])
            if not row or any(
                (
                    row["user_id"] != document["user_id"],
                    row["name"] != document["name"],
                    row["definition_json"] != json.loads(document["definitionJson"]),
                    row["enabled_for_ai"] != document["enabled_for_ai"],
                    row["version"] != document["version"],
                    row["source_run_id"] != document.get("source_run_id"),
                    row["deleted"] != document["deleted"],
                )
            ):
                raise ValueError(f"Saved strategy import mismatch: {document['id']}")
        return
    if table == "orderIntents":
        actual = {
            (row["account_id"], row["client_order_id"]): row
            for row in connection.execute("select * from trade.order_intents").fetchall()
        }
        for document in source:
            row = actual.get((document["accountId"], document["clientOrderId"]))
            if not row or any(
                (
                    row["payload"] != document["payload"],
                    row["context"] != document.get("context"),
                    row["materialized"] != document.get("materialized", False),
                    row["outcome"] != document["outcome"],
                    row["unresolved"] != document["unresolved"],
                    row["created_at"] != datetime.fromisoformat(iso_from_ms(document["_creationTime"])),
                    row["updated_at"] != datetime.fromisoformat(iso_from_ms(document["updatedAt"])),
                )
            ):
                raise ValueError(f"Order intent import mismatch: {document['clientOrderId']}")
        return
    if table == "exchangeFills":
        actual = {
            (row["account_id"], row["fill_id"]): row
            for row in connection.execute("select * from trade.exchange_fills").fetchall()
        }
        for document in source:
            row = actual.get((document["accountId"], document["fillId"]))
            expected_fee = Decimal(document["commission"]) if document.get("commission") is not None else None
            if not row or any(
                (
                    row["product_id"] != document["productId"],
                    row["order_id"] != document["orderId"],
                    row["side"] != document["side"],
                    row["quantity"] != Decimal(document["quantity"]),
                    row["price"] != Decimal(document["price"]),
                    row["commission"] != expected_fee,
                    row["occurred_at"] != datetime.fromisoformat(document["occurredAt"].replace("Z", "+00:00")),
                )
            ):
                raise ValueError(f"Exchange fill import mismatch: {document['fillId']}")
        return
    if table == "productClaims":
        actual = {
            (row["account_id"], row["product_id"]): row["strategy_id"]
            for row in connection.execute(
                "select account_id,product_id,strategy_id from trade.product_claims"
            ).fetchall()
        }
        for document in source:
            if actual.get((document["accountId"], document["productId"])) != document["strategyId"]:
                raise ValueError(f"Product claim import mismatch: {document['productId']}")
        return
    raise ValueError(f"Unexpected verification table: {table}")


def run(path: Path, database_url: str | None, *, apply: bool, source_paused: bool) -> dict[str, int]:
    with zipfile.ZipFile(path) as archive:
        data = {table: documents(archive, table) for table in TABLES}
    counts = {table: len(rows) for table, rows in data.items()}
    if not apply:
        return counts
    if not source_paused or not database_url:
        raise ValueError("Import requires --source-paused and LOCAL_DATABASE_URL")
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("select pg_advisory_xact_lock(hashtext('trade-local-import'))")
        connection.row_factory = dict_row
        for table in TABLES:
            target = target_table(table)
            count = connection.execute(
                sql.SQL("select count(*) as count from {}").format(sql.Identifier("trade", target))
            ).fetchone()["count"]
            if count:
                raise ValueError(f"Destination is not empty: {table}")
        for table in TABLES:
            import_rows(connection, table, data[table])
            target = target_table(table)
            actual = connection.execute(
                sql.SQL("select count(*) as count from {}").format(sql.Identifier("trade", target))
            ).fetchone()["count"]
            if actual != counts[table]:
                raise ValueError(f"Import count mismatch: {table}")
            verify_rows(connection, table, data[table])
        for strategy in data["strategies"]:
            if strategy["status"] in {"completed", "cancelled"}:
                connection.execute("select trade.mark_closed_recovery(%s)", (strategy["externalId"],))
        connection.execute(
            """create table if not exists trade.import_manifest
                   (table_name text primary key, row_count bigint not null, sha256 text not null,
                    imported_at timestamptz not null default now())"""
        )
        for table in TABLES:
            connection.execute(
                "insert into trade.import_manifest (table_name,row_count,sha256) values (%s,%s,%s)",
                (table, counts[table], checksum(data[table])),
            )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source-paused", action="store_true")
    args = parser.parse_args()
    counts = run(args.export, os.getenv("LOCAL_DATABASE_URL"), apply=args.apply, source_paused=args.source_paused)
    print(json.dumps({"counts": counts, "applied": args.apply}))


if __name__ == "__main__":
    main()
