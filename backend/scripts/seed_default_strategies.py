from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from app.default_strategies import default_strategy_definitions

BACKEND_DIR = Path(__file__).resolve().parents[1]


def database_url() -> str:
    load_dotenv(BACKEND_DIR / ".env", override=False)
    raw = os.getenv("SUPABASE_DB_URL")
    if not raw:
        raise RuntimeError("SUPABASE_DB_URL is not configured")
    normalized = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def target_users(connection: psycopg.Connection, explicit_user_id: str | None) -> list[str]:
    if explicit_user_id:
        return [explicit_user_id]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select u.id::text
            from auth.users u
            where not exists (
              select 1 from public.saved_strategies s where s.user_id = u.id
            )
            order by u.created_at desc
            """
        )
        return [row[0] for row in cursor.fetchall()]


def seed(user_id: str | None = None) -> tuple[int, int]:
    definitions = default_strategy_definitions()
    inserted = 0
    users_seeded = 0
    with psycopg.connect(database_url()) as connection:
        users = target_users(connection, user_id)
        for target_user in users:
            user_inserted = 0
            with connection.cursor() as cursor:
                for definition in definitions:
                    payload = definition.model_dump(mode="json", exclude_none=True)
                    cursor.execute(
                        """
                        select 1 from public.saved_strategies
                        where user_id = %s and name = %s
                        limit 1
                        """,
                        (target_user, definition.name),
                    )
                    if cursor.fetchone():
                        continue
                    cursor.execute(
                        """
                        insert into public.saved_strategies
                          (user_id, name, definition_json, enabled_for_ai)
                        values (%s, %s, %s, true)
                        """,
                        (target_user, definition.name, Jsonb(payload)),
                    )
                    user_inserted += 1
            if user_inserted:
                users_seeded += 1
                inserted += user_inserted
        connection.commit()
    return users_seeded, inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the eight approved BTC option strategy templates")
    parser.add_argument("--user-id", help="Seed one Supabase auth user instead of every empty strategy library")
    args = parser.parse_args()
    users, strategies = seed(args.user_id)
    print(f"Seeded {strategies} strategies for {users} user(s).")


if __name__ == "__main__":
    main()
