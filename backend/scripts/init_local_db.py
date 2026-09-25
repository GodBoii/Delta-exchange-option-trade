"""Apply versioned local PostgreSQL migrations before starting the trading writer."""

import argparse
import hashlib
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db"


def apply_migrations(database_url: str) -> list[str]:
    if not database_url:
        raise ValueError("LOCAL_DATABASE_URL is required")
    applied = []
    with psycopg.connect(database_url, autocommit=False) as connection, connection.transaction():
        connection.execute("select pg_advisory_xact_lock(hashtext('trade-local-schema'))")
        connection.execute(
            """create table if not exists public.trade_schema_migrations (
                       name text primary key, sha256 text not null, applied_at timestamptz not null default now())"""
        )
        current = dict(connection.execute("select name, sha256 from public.trade_schema_migrations").fetchall())
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
            source = path.read_bytes()
            digest = hashlib.sha256(source).hexdigest()
            if path.name in current:
                if current[path.name] != digest:
                    raise ValueError(f"Migration changed after application: {path.name}")
                continue
            connection.execute(source.decode("utf-8"))
            connection.execute(
                "insert into public.trade_schema_migrations (name,sha256) values (%s,%s)",
                (path.name, digest),
            )
            applied.append(path.name)
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("LOCAL_DATABASE_URL"))
    args = parser.parse_args()
    for name in apply_migrations(args.database_url):
        print(f"Applied {name}")


if __name__ == "__main__":
    main()
