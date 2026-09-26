"""Copy analysis reports and Agno sessions from Supabase into the local ``ai`` schema.

Run inside the analysis-service image, which has Agno and the network routes:

  SUPABASE_DB_URL=... python -m scripts.import_supabase_ai          # counts only
  SUPABASE_DB_URL=... python -m scripts.import_supabase_ai --apply  # copy and verify

The destination must be empty. Chart images are not copied: their Supabase storage is
retired, so chart references are removed from the imported market snapshots. The
source is only read.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from typing import Any

import psycopg
from agno.db.postgres import PostgresDb
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from news_agent.config import AUTOMATION_SESSION_TABLE, DB_SCHEMA, SESSION_TABLE, NewsAgentSettings

REPORT_COLUMNS = (
    "id", "snapshot_id", "report_markdown", "member_responses", "tool_calls",
    "market_json", "account_json", "created_at", "updated_at",
)
BATCH = 25


def source_url() -> str:
    raw = os.getenv("SUPABASE_DB_URL")
    if not raw:
        raise RuntimeError("SUPABASE_DB_URL is required for the one-time import")
    url = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    return url if "sslmode=" in url else f"{url}{'&' if '?' in url else '?'}sslmode=require"


def create_session_tables(settings: NewsAgentSettings) -> None:
    for table in (SESSION_TABLE, AUTOMATION_SESSION_TABLE):
        db = PostgresDb(
            db_url=settings.require_database_url(), db_schema=DB_SCHEMA, session_table=table, create_schema=True
        )
        try:
            if db._get_table("sessions", create_table_if_not_found=True) is None:
                raise RuntimeError(f"Agno could not create ai.{table}")
        finally:
            db.close()


def columns(connection: psycopg.Connection, schema: str, table: str) -> list[str]:
    rows = connection.execute(
        """select column_name from information_schema.columns
           where table_schema=%s and table_name=%s order by ordinal_position""",
        (schema, table),
    ).fetchall()
    return [row[0] for row in rows]


def digest(connection: psycopg.Connection, query: sql.Composable) -> tuple[int, str]:
    value = hashlib.sha256()
    count = 0
    for (key,) in connection.execute(query):
        value.update(f"{key}\n".encode())
        count += 1
    return count, value.hexdigest()


def adapt(row: dict[str, Any]) -> dict[str, Any]:
    return {key: Jsonb(value) if isinstance(value, dict | list) else value for key, value in row.items()}


def copy_table(
    source: psycopg.Connection,
    target: psycopg.Connection,
    select: sql.Composable,
    table: sql.Identifier,
    names: list[str],
) -> int:
    insert = sql.SQL("insert into {} ({}) values ({})").format(
        table,
        sql.SQL(", ").join(sql.Identifier(name) for name in names),
        sql.SQL(", ").join(sql.Placeholder(name) for name in names),
    )
    copied = 0
    with source.cursor(name="ai_import", row_factory=dict_row) as reader:
        reader.itersize = BATCH
        reader.execute(select)
        with target.cursor() as writer:
            while rows := reader.fetchmany(BATCH):
                writer.executemany(insert, [adapt(row) for row in rows])
                copied += len(rows)
    return copied


def run(apply: bool) -> dict[str, int]:
    settings = NewsAgentSettings.load()
    if apply:
        create_session_tables(settings)
    counts: dict[str, int] = {}
    with psycopg.connect(source_url()) as source, psycopg.connect(settings.psycopg_url()) as target:
        source.execute("set transaction read only")
        tables = [("public", "analysis_reports", ("ai", "analysis_reports"))] + [
            ("ai", name, ("ai", name)) for name in (SESSION_TABLE, AUTOMATION_SESSION_TABLE)
        ]
        for schema, name, _target in tables:
            counts[f"{schema}.{name}"] = source.execute(
                sql.SQL("select count(*) from {}").format(sql.Identifier(schema, name))
            ).fetchone()[0]
        if not apply:
            return counts
        for _, _, (target_schema, target_name) in tables:
            existing = target.execute(
                sql.SQL("select count(*) from {}").format(sql.Identifier(target_schema, target_name))
            ).fetchone()[0]
            if existing:
                raise RuntimeError(f"{target_schema}.{target_name} is not empty; refusing to merge")
        report_select = sql.SQL(
            "select id, snapshot_id, report_markdown, member_responses, tool_calls, "
            "market_json - 'chartImages' as market_json, account_json, created_at, updated_at "
            "from public.analysis_reports order by id"
        )
        copy_table(source, target, report_select, sql.Identifier("ai", "analysis_reports"), list(REPORT_COLUMNS))
        for name in (SESSION_TABLE, AUTOMATION_SESSION_TABLE):
            shared = [column for column in columns(source, "ai", name) if column in set(columns(target, "ai", name))]
            select = sql.SQL("select {} from {} order by session_id").format(
                sql.SQL(", ").join(sql.Identifier(column) for column in shared), sql.Identifier("ai", name)
            )
            copy_table(source, target, select, sql.Identifier("ai", name), shared)
        checks = [
            ("analysis_reports", "public", "id::text", "id"),
            (SESSION_TABLE, "ai", "session_id", "session_id"),
            (AUTOMATION_SESSION_TABLE, "ai", "session_id", "session_id"),
        ]
        for name, schema, key, order in checks:
            source_digest = digest(
                source,
                sql.SQL("select {} from {} order by {}").format(
                    sql.SQL(key), sql.Identifier(schema, name), sql.Identifier(order)
                ),
            )
            target_digest = digest(
                target,
                sql.SQL("select {} from {} order by {}").format(
                    sql.SQL(key), sql.Identifier("ai", name), sql.Identifier(order)
                ),
            )
            if source_digest != target_digest:
                raise RuntimeError(f"Verification failed for {name}; nothing was committed")
        target.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Copy into the empty local ai schema")
    args = parser.parse_args()
    counts = run(args.apply)
    for table, count in counts.items():
        print(f"{table}: {count}")
    print("Imported and verified" if args.apply else "Dry run; nothing copied")


if __name__ == "__main__":
    main()
