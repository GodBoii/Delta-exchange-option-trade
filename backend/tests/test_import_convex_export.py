"""Rehearse a Convex ZIP import into an empty disposable local database."""

import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from scripts.import_convex_export import TABLES, run
from scripts.init_local_db import apply_migrations

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


def database_url(base: str, name: str) -> str:
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def test_import_preserves_every_record_and_queues_recovery(tmp_path: Path):
    admin_url = os.environ["TEST_LOCAL_DATABASE_URL"]
    name = "trade_import_" + uuid4().hex[:12]
    target_url = database_url(admin_url, name)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        apply_migrations(target_url)
        owner, strategy_id, saved_id = (str(uuid4()) for _ in range(3))
        now = datetime.now(UTC)
        millis = int(now.timestamp() * 1000)
        timestamp = datetime.fromtimestamp(millis / 1000, UTC).isoformat()
        data = {table: [] for table in TABLES}
        data["users"] = [{
            "userId": owner, "capital": {"allocation_mode": "half_balance", "capital_amount": None},
            "automation": {"enabled": True, "model_id": "test", "minimum_follow_up_minutes": 5,
                           "maximum_agent_runs_per_day": 3},
            "connection": None, "createdAt": timestamp, "updatedAt": timestamp,
            "_id": "convex-user-id", "_creationTime": millis,
        }]
        data["systemSettings"] = [{
            "key": "main", "ownerUserId": owner, "outboundIp": "127.0.0.1", "ipCheckedAt": timestamp,
            "analysis": {"enabled": True, "model_id": "test", "minimum_follow_up_minutes": 5,
                         "maximum_agent_runs_per_day": 3},
            "_creationTime": millis,
        }]
        data["savedStrategies"] = [{
            "id": saved_id, "user_id": None, "name": "Saved", "definitionJson": '{"name":"Saved"}',
            "source_run_id": None, "version": 1, "enabled_for_ai": True, "deleted": False,
            "created_at": timestamp, "updated_at": timestamp, "_creationTime": millis,
        }]
        strategy = {"id": strategy_id, "user_id": owner, "status": "scheduled",
                    "name": "Saved", "created_at": timestamp, "entry_at": timestamp}
        data["strategies"] = [{
            "externalId": strategy_id, "owner": owner, "status": "scheduled",
            "relation": "", "uniqueKey": strategy_id, "created": millis, "_creationTime": millis,
            "rowJson": json.dumps(strategy),
        }]
        data["orderIntents"] = [{
            "accountId": "india:1", "clientOrderId": "order1", "payload": "{}",
            "context": {"strategyId": strategy_id}, "materialized": False,
            "outcome": {"kind": "unknown"}, "unresolved": True,
            "_creationTime": millis, "updatedAt": millis,
        }]
        data["exchangeFills"] = [{
            "accountId": "india:1", "fillId": "fill1", "productId": "123",
            "orderId": "exchange-order1", "side": "buy", "quantity": "1", "price": "1.25",
            "commission": None, "occurredAt": timestamp,
        }]
        data["productClaims"] = [{
            "accountId": "india:1", "productId": "123", "strategyId": strategy_id,
        }]
        archive = tmp_path / "sample.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for table, rows in data.items():
                output.writestr(f"{table}/documents.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        assert run(archive, None, apply=False, source_paused=False) == {
            table: len(rows) for table, rows in data.items()
        }
        assert run(archive, target_url, apply=True, source_paused=True)["strategies"] == 1
        with psycopg.connect(target_url) as connection:
            assert connection.execute("select count(*) from trade.recovery_outbox").fetchone()[0] == 7
        with pytest.raises(ValueError, match="not empty"):
            run(archive, target_url, apply=True, source_paused=True)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))
