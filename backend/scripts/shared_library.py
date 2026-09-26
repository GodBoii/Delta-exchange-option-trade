"""Review or update the shared strategy templates in the local PostgreSQL library.

Commands print the planned change unless ``--apply`` is given:

  seed           create canonical shared templates that are missing, by name
  descriptions   copy canonical descriptions onto existing shared templates
  take-profit    set every shared template to a 50% take profit (refused while runs are open)

Every update increments the template version so open editors detect the change.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.default_strategies import default_strategy_definitions
from app.models import StrategyDefinition

# Stable identities for templates added after the original catalog, so reruns stay idempotent.
FIXED_IDS = {
    "Bull put credit spread": "10000000-0000-4000-8000-000000000014",
    "Bear call credit spread": "10000000-0000-4000-8000-000000000015",
}
OPEN_STATUSES = ("scheduled", "executing_entry", "active", "executing_exit", "attention")


def shared_templates(cursor: psycopg.Cursor) -> list[dict[str, Any]]:
    cursor.execute(
        """select id::text as id, name, definition_json, version, enabled_for_ai
           from trade.saved_strategies where user_id is null and not deleted
           order by name for update"""
    )
    return cursor.fetchall()


def update_definition(cursor: psycopg.Cursor, row: dict[str, Any], definition: dict[str, Any]) -> None:
    StrategyDefinition.model_validate(definition)
    cursor.execute(
        """update trade.saved_strategies
           set definition_json=%s, version=version+1, updated_at=now()
           where id=%s and version=%s""",
        (Jsonb(definition), row["id"], row["version"]),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"{row['name']} changed while it was being updated")


def seed(cursor: psycopg.Cursor, apply: bool) -> int:
    existing = {row["name"] for row in shared_templates(cursor)}
    now = datetime.now(UTC)
    created = 0
    for item in default_strategy_definitions(now):
        if item.name in existing:
            continue
        print(f"{'Creating' if apply else 'Would create'}: {item.name}")
        created += 1
        if apply:
            cursor.execute(
                """insert into trade.saved_strategies
                   (id,user_id,name,definition_json,enabled_for_ai,version,source_run_id,created_at,updated_at)
                   values (%s,null,%s,%s,true,1,null,%s,%s)""",
                (
                    FIXED_IDS.get(item.name, str(uuid4())),
                    item.name,
                    Jsonb(item.model_dump(mode="json", exclude_none=True)),
                    now,
                    now,
                ),
            )
    return created


def descriptions(cursor: psycopg.Cursor, apply: bool) -> int:
    expected = {item.name: item.description for item in default_strategy_definitions(datetime.now(UTC))}
    rows = shared_templates(cursor)
    unknown = sorted(row["name"] for row in rows if row["name"] not in expected)
    if unknown:
        raise RuntimeError(f"Shared strategies have no canonical description: {', '.join(unknown)}")
    changed = 0
    for row in rows:
        definition = dict(row["definition_json"])
        if definition.get("description") == expected[row["name"]]:
            continue
        definition["description"] = expected[row["name"]]
        changed += 1
        print(f"{'Updating' if apply else 'Would update'} {row['name']} (version {row['version']})")
        if apply:
            update_definition(cursor, row, definition)
    return changed


def take_profit(cursor: psycopg.Cursor, apply: bool) -> int:
    rows = shared_templates(cursor)
    if not rows:
        raise RuntimeError("The shared strategy catalog is empty")
    if apply:
        cursor.execute("select count(*) as count from trade.strategies where status = any(%s)", (list(OPEN_STATUSES),))
        open_runs = cursor.fetchone()["count"]
        if open_runs:
            raise RuntimeError(f"Refusing to update take profit while {open_runs} strategy runs remain open")
    changed = 0
    for row in rows:
        definition = dict(row["definition_json"])
        if definition.get("takeProfitPercent") == 50:
            continue
        definition["takeProfitPercent"] = 50
        changed += 1
        print(f"{'Updating' if apply else 'Would update'} {row['name']} (version {row['version']}) to 50%")
        if apply:
            update_definition(cursor, row, definition)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("seed", "descriptions", "take-profit"))
    parser.add_argument("--apply", action="store_true", help="Commit the changes")
    parser.add_argument("--database-url", default=os.getenv("LOCAL_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise RuntimeError("LOCAL_DATABASE_URL is required")
    operation = {"seed": seed, "descriptions": descriptions, "take-profit": take_profit}[args.command]
    with psycopg.connect(args.database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        changed = operation(cursor, args.apply)
        if not args.apply:
            connection.rollback()
    print(f"{changed} shared templates {'changed' if args.apply else 'would change'}")


if __name__ == "__main__":
    main()
