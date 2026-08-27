from __future__ import annotations

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


def seed() -> int:
    definitions = default_strategy_definitions()
    inserted = 0
    with psycopg.connect(database_url()) as connection:
        with connection.cursor() as cursor:
            for definition in definitions:
                payload = definition.model_dump(mode="json", exclude_none=True)
                cursor.execute(
                    """
                    select 1 from public.saved_strategies
                    where is_default = true and name = %s
                    limit 1
                    """,
                    (definition.name,),
                )
                if cursor.fetchone():
                    continue
                cursor.execute(
                    """
                    insert into public.saved_strategies
                      (user_id, name, definition_json, enabled_for_ai, is_default)
                    values (null, %s, %s, true, true)
                    """,
                    (definition.name, Jsonb(payload)),
                )
                inserted += 1
        connection.commit()
    return inserted


def main() -> None:
    strategies = seed()
    print(f"Seeded {strategies} shared default strategies.")


if __name__ == "__main__":
    main()
