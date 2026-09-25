"""A Convex restore starts paused and does not echo imported rows back to Convex."""

import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from scripts.init_local_db import apply_migrations
from scripts.restore_local_from_recovery import restore

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


def test_restored_database_requires_manual_reconciliation(tmp_path: Path):
    admin_url = os.environ["TEST_LOCAL_DATABASE_URL"]
    name = "trade_restore_" + uuid4().hex[:12]
    parts = urlsplit(admin_url)
    target_url = urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        apply_migrations(target_url)
        user_id = str(uuid4())
        payload = {
            "user_id": user_id,
            "connection": None,
            "automation": {"enabled": False},
            "capital": {"allocation_mode": "half_balance"},
            "record": {"userId": user_id},
        }
        export = tmp_path / "recovery.zip"
        with zipfile.ZipFile(export, "w") as archive:
            archive.writestr(
                "recoveryRecords/documents.jsonl",
                json.dumps(
                    {
                        "entityType": "users",
                        "entityKey": user_id,
                        "revision": 3,
                        "payloadJson": json.dumps(payload),
                    }
                )
                + "\n",
            )
            archive.writestr(
                "recoveryManifests/documents.jsonl",
                json.dumps(
                    {
                        "day": "2026-09-25",
                        "lastOutboxId": 42,
                        "checksum": "a" * 64,
                        "countsJson": json.dumps({"users": 1}),
                    }
                )
                + "\n",
            )
        result = restore(export, target_url, None)
        assert result["tradingPaused"] is True
        assert result["countDifferences"] == {}
        with psycopg.connect(target_url) as connection:
            assert connection.execute("select pending from trade.recovery_gate where key='main'").fetchone()[0]
            assert (
                connection.execute("select revision from trade.users where user_id=%s", (user_id,)).fetchone()[0] == 3
            )
            assert connection.execute("select count(*) from trade.recovery_outbox").fetchone()[0] == 0
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))
