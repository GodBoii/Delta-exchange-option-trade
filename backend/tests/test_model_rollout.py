"""Model rollout changes pending work without rewriting execution history."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_LOCAL_DATABASE_URL"), reason="No isolated local PostgreSQL test URL"
)


@pytest.mark.parametrize(
    ("migration_name", "previous", "target"),
    [
        ("013_deepseek_flash_1017.sql", "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash-1017"),
        ("015_deepseek_flash.sql", "deepseek/deepseek-v4.1-flash-1017", "deepseek/deepseek-v4.1-flash"),
    ],
)
def test_model_rollout_preserves_flags_history_and_idempotence(migration_name, previous, target):
    user = str(uuid4())
    jobs = {status: str(uuid4()) for status in ("scheduled", "running", "completed")}
    migration = Path(__file__).resolve().parents[1] / "db" / migration_name
    settings = {"enabled": False, "model_id": previous, "asset_enabled": {"BTC": False, "ETH": True}}

    with psycopg.connect(os.environ["TEST_LOCAL_DATABASE_URL"]) as connection:
        try:
            connection.execute(
                """insert into trade.system_settings (key,owner_user_id,analysis) values ('main',%s,%s)
                   on conflict (key) do update set analysis=excluded.analysis""",
                (user, Jsonb(settings)),
            )
            connection.execute(
                "insert into trade.users (user_id,automation,capital,record) values (%s,%s,'{}',%s)",
                (user, Jsonb(settings), Jsonb({"automation": settings, "displayName": "Preserved"})),
            )
            for status, job in jobs.items():
                connection.execute(
                    "insert into trade.analysis_jobs (id,owner_id,status,created_at,data) values (%s,%s,%s,now(),%s)",
                    (job, user, status, Jsonb({"id": job, "model_id": previous, "status": status})),
                )

            connection.execute(migration.read_text())
            expected = {**settings, "model_id": target}
            assert connection.execute(
                "select analysis from trade.system_settings where key='main'"
            ).fetchone()[0] == expected
            automation, record, revision = connection.execute(
                "select automation,record,revision from trade.users where user_id=%s", (user,)
            ).fetchone()
            assert automation == expected
            assert record == {"automation": expected, "displayName": "Preserved"}
            for status, job in jobs.items():
                data = connection.execute("select data from trade.analysis_jobs where id=%s", (job,)).fetchone()[0]
                assert data == {"id": job, "model_id": target if status == "scheduled" else previous, "status": status}

            connection.execute(migration.read_text())
            assert connection.execute(
                "select revision from trade.users where user_id=%s", (user,)
            ).fetchone()[0] == revision
        finally:
            connection.rollback()
