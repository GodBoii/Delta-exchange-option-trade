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

from app.default_strategies import builtin_strategy_definitions
from app.exit_schedule import validate_template

# Stable identities for templates added after the original catalog, so reruns stay idempotent.
FIXED_IDS = {
    "Bull put credit spread": "10000000-0000-4000-8000-000000000014",
    "Bear call credit spread": "10000000-0000-4000-8000-000000000015",
}
ETH_FIXED_IDS = {
    "ETH Long call": "20000000-0000-4000-8000-000000000001",
    "ETH Long put": "20000000-0000-4000-8000-000000000002",
    "ETH Long ATM straddle": "20000000-0000-4000-8000-000000000003",
    "ETH Short ATM straddle": "20000000-0000-4000-8000-000000000004",
    "ETH Long ATM straddle - next-day expiry": "20000000-0000-4000-8000-000000000005",
    "ETH Short ATM straddle - next-day expiry": "20000000-0000-4000-8000-000000000006",
}
OPEN_STATUSES = ("scheduled", "executing_entry", "active", "executing_exit", "attention")
RETIRED_SUFFIX = " - next-day expiry"


def shared_templates(cursor: psycopg.Cursor) -> list[dict[str, Any]]:
    cursor.execute(
        """select id::text as id, name, definition_json, version, enabled_for_ai
           from trade.saved_strategies where user_id is null and not deleted
           order by name for update"""
    )
    return cursor.fetchall()


def update_definition(cursor: psycopg.Cursor, row: dict[str, Any], definition: dict[str, Any]) -> None:
    definition = validate_template(definition)
    cursor.execute(
        """update trade.saved_strategies
           set definition_json=%s, version=version+1, updated_at=now()
           where id=%s and version=%s""",
        (Jsonb(definition), row["id"], row["version"]),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"{row['name']} changed while it was being updated")


def seed(cursor: psycopg.Cursor, apply: bool) -> int:
    # Deleted templates count as existing: retiring a template must not be undone by a reseed.
    cursor.execute("select name from trade.saved_strategies where user_id is null")
    existing = {row["name"] for row in cursor.fetchall()}
    now = datetime.now(UTC)
    created = 0
    for item in builtin_strategy_definitions(now):
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
                    {**FIXED_IDS, **ETH_FIXED_IDS}.get(item.name, str(uuid4())),
                    item.name,
                    Jsonb(validate_template(item.model_dump(mode="json", exclude_none=True))),
                    now,
                    now,
                ),
            )
    return created


def descriptions(cursor: psycopg.Cursor, apply: bool) -> int:
    expected = {item.name: item.description for item in builtin_strategy_definitions(datetime.now(UTC))}
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


def exit_templates(cursor: psycopg.Cursor, apply: bool) -> int:
    """Convert saved templates and retire duplicate expiry-only entries."""
    cursor.execute(
        """select id::text as id,user_id,name,definition_json,version from trade.saved_strategies
           where not deleted order by id for update"""
    )
    rows = cursor.fetchall()
    canonical = {item.name: item.description for item in builtin_strategy_definitions(datetime.now(UTC))}
    names = {row["name"] for row in rows}
    duplicate_ids = [
        row["id"]
        for row in rows
        if row["name"].endswith(RETIRED_SUFFIX) and row["name"].removesuffix(RETIRED_SUFFIX) in names
    ]
    if apply:
        cursor.execute(
            """select count(*) as count from trade.strategy_proposals
               where status='scheduled' and activation_time>now()""",
        )
        if cursor.fetchone()["count"]:
            raise RuntimeError("A future proposal is pending; retry after its activation recheck")
    changed = 0
    for row in rows:
        if row["id"] in duplicate_ids:
            print(f"{'Retiring' if apply else 'Would retire'} {row['name']}")
            if apply:
                cursor.execute(
                    """update trade.saved_strategies set deleted=true,enabled_for_ai=false,
                       version=version+1,updated_at=now() where id=%s and version=%s""",
                    (row["id"], row["version"]),
                )
            changed += 1
            continue
        definition = validate_template(row["definition_json"])
        if row["user_id"] is None and row["name"] in canonical:
            definition["description"] = canonical[row["name"]]
        if definition == row["definition_json"]:
            continue
        print(f"{'Converting' if apply else 'Would convert'} {row['name']}")
        if apply:
            cursor.execute(
                """update trade.saved_strategies set definition_json=%s,version=version+1,
                   updated_at=now() where id=%s and version=%s""",
                (Jsonb(definition), row["id"], row["version"]),
            )
        changed += 1
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("seed", "descriptions", "take-profit", "exit-templates"))
    parser.add_argument("--apply", action="store_true", help="Commit the changes")
    parser.add_argument("--database-url", default=os.getenv("LOCAL_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise RuntimeError("LOCAL_DATABASE_URL is required")
    operation = {
        "seed": seed,
        "descriptions": descriptions,
        "take-profit": take_profit,
        "exit-templates": exit_templates,
    }[args.command]
    with psycopg.connect(args.database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        changed = operation(cursor, args.apply)
        if not args.apply:
            connection.rollback()
    print(f"{changed} shared templates {'changed' if args.apply else 'would change'}")


if __name__ == "__main__":
    main()
