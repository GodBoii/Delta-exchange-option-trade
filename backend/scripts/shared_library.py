"""Review or update the shared strategy templates in the local PostgreSQL library.

Commands print the planned change unless ``--apply`` is given:

  seed           create canonical shared templates that are missing, by name
  descriptions   copy canonical descriptions onto existing shared templates
  take-profit    set every shared template to a 50% take profit (refused while runs are open)
  emergency-stop set shared short-leg templates to a 300% emergency stop for future entries
  eth-catalog    mirror current BTC rules into eight ETH templates; retire ETH straddles
  retire-long-straddle  disable and soft-delete long ATM straddle templates for all owners

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

from app.default_strategies import (
    ETH_INSTRUMENT,
    ETH_TEMPLATE_SOURCES,
    builtin_strategy_definitions,
    is_retired_long_atm_straddle,
)
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
    "ETH Long strangle": "20000000-0000-4000-8000-000000000007",
    "ETH Short strangle": "20000000-0000-4000-8000-000000000008",
    "ETH Short OTM put": "20000000-0000-4000-8000-000000000009",
    "ETH Short OTM call": "20000000-0000-4000-8000-000000000010",
    "ETH Long ITM call": "20000000-0000-4000-8000-000000000011",
    "ETH Long ITM put": "20000000-0000-4000-8000-000000000012",
}
ETH_RETIRED_NAMES = ("ETH Long ATM straddle", "ETH Short ATM straddle")
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


def emergency_stop(cursor: psycopg.Cursor, apply: bool) -> int:
    rows = shared_templates(cursor)
    if not rows:
        raise RuntimeError("The shared strategy catalog is empty")
    changed = 0
    for row in rows:
        definition = dict(row["definition_json"])
        if not any(leg.get("position") == "sell" for leg in definition.get("legs", [])):
            continue
        if definition.get("emergencyStopLossPercent") == 300:
            continue
        definition["emergencyStopLossPercent"] = 300
        changed += 1
        print(f"{'Updating' if apply else 'Would update'} {row['name']} (version {row['version']}) to 300%")
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
    shared_names = {row["name"] for row in rows if row["user_id"] is None}
    duplicate_ids = [
        row["id"]
        for row in rows
        if row["user_id"] is None
        and row["name"].endswith(RETIRED_SUFFIX)
        and row["name"].removesuffix(RETIRED_SUFFIX) in shared_names
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


def eth_catalog(cursor: psycopg.Cursor, apply: bool) -> int:
    """Copy missing ETH shapes from live BTC rules in one transaction.

Existing ETH counterparts must already match their BTC source. Refuse drift or
an explicitly retired counterpart instead of silently changing its risk rules.
Retirement only changes shared templates; run definitions and history are intact.
"""
    cursor.execute("select pg_advisory_xact_lock(hashtext('trade-eth-catalog'))")
    cursor.execute(
        """select id::text as id,name,definition_json,version,enabled_for_ai,deleted
           from trade.saved_strategies where user_id is null order by name for update"""
    )
    rows = cursor.fetchall()
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(row["name"], []).append(row)
    planned = []
    for name in ETH_TEMPLATE_SOURCES:
        sources = by_name.get(name, [])
        if len(sources) != 1 or sources[0]["deleted"]:
            raise RuntimeError(f"Expected one current shared BTC source: {name}")
        source = sources[0]
        definition = source["definition_json"]
        if definition.get("instrument", {}).get("underlying") != "BTC":
            raise RuntimeError(f"Shared source is not a BTC strategy: {name}")
        eth_name = f"ETH {name}"
        counterpart = validate_template({
            **definition,
            "name": eth_name,
            "description": definition.get("description", "").replace("BTC", "ETH"),
            "instrument": dict(ETH_INSTRUMENT),
            "enabledForAi": source["enabled_for_ai"],
        })
        existing = by_name.get(eth_name, [])
        if existing:
            if len(existing) != 1 or existing[0]["deleted"]:
                raise RuntimeError(f"ETH counterpart is duplicated or explicitly retired: {eth_name}")
            current = validate_template(existing[0]["definition_json"])
            if (
                {k: v for k, v in current.items() if k != "version"}
                != {k: v for k, v in counterpart.items() if k != "version"}
                or existing[0]["enabled_for_ai"] != source["enabled_for_ai"]
            ):
                raise RuntimeError(f"Existing ETH rules differ from BTC; review before updating: {eth_name}")
            continue
        counterpart["version"] = 1
        planned.append((eth_name, counterpart, source["enabled_for_ai"]))
    retired = [row for row in rows if row["name"] in ETH_RETIRED_NAMES and not row["deleted"]]
    if apply and retired:
        cursor.execute(
            """select count(*) as count from trade.strategy_proposals
               where status='scheduled' and activation_time>now()
                 and data->>'saved_strategy_id'=any(%s)""",
            ([row["id"] for row in retired],),
        )
        if cursor.fetchone()["count"]:
            raise RuntimeError("An ETH straddle proposal is pending; retry after its activation recheck")
    for name, definition, enabled in planned:
        print(f"{'Creating' if apply else 'Would create'}: {name}")
        if apply:
            cursor.execute(
                """insert into trade.saved_strategies
                   (id,user_id,name,definition_json,enabled_for_ai,version,created_at,updated_at)
                   values (%s,null,%s,%s,%s,1,now(),now())""",
                (ETH_FIXED_IDS[name], name, Jsonb(definition), enabled),
            )
    for row in retired:
        print(f"{'Retiring' if apply else 'Would retire'}: {row['name']}")
        if apply:
            cursor.execute(
                """update trade.saved_strategies set deleted=true,enabled_for_ai=false,
                   definition_json=jsonb_set(definition_json,'{enabledForAi}','false'::jsonb),
                   version=version+1,updated_at=now() where id=%s and version=%s""",
                (row["id"], row["version"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"{row['name']} changed during retirement")
    return len(planned) + len(retired)


def retire_long_straddle(cursor: psycopg.Cursor, apply: bool) -> int:
    """Retire templates while retaining run definitions, orders, and exit monitoring."""
    cursor.execute(
        """select id::text as id,name,definition_json,version,deleted,enabled_for_ai
           from trade.saved_strategies order by id for update"""
    )
    retired = [
        row for row in cursor.fetchall()
        if is_retired_long_atm_straddle({**row["definition_json"], "name": row["name"]})
        and (not row["deleted"] or row["enabled_for_ai"] or row["definition_json"].get("enabledForAi") is not False)
    ]
    for row in retired:
        print(f"{'Retiring' if apply else 'Would retire'}: {row['name']} ({row['id']})")
        if apply:
            cursor.execute(
                """update trade.saved_strategies set deleted=true,enabled_for_ai=false,
                   definition_json=jsonb_set(definition_json,'{enabledForAi}','false'::jsonb),
                   version=version+1,updated_at=now() where id=%s and version=%s""",
                (row["id"], row["version"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"{row['name']} changed during retirement")
    return len(retired)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "command", choices=(
            "seed", "descriptions", "take-profit", "emergency-stop", "exit-templates", "eth-catalog",
            "retire-long-straddle",
        )
    )
    parser.add_argument("--apply", action="store_true", help="Commit the changes")
    parser.add_argument("--database-url", default=os.getenv("LOCAL_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise RuntimeError("LOCAL_DATABASE_URL is required")
    operation = {
        "seed": seed,
        "descriptions": descriptions,
        "take-profit": take_profit,
        "emergency-stop": emergency_stop,
        "exit-templates": exit_templates,
        "eth-catalog": eth_catalog,
        "retire-long-straddle": retire_long_straddle,
    }[args.command]
    with psycopg.connect(args.database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        changed = operation(cursor, args.apply)
        if not args.apply:
            connection.rollback()
    print(f"{changed} shared templates {'changed' if args.apply else 'would change'}")


if __name__ == "__main__":
    main()
