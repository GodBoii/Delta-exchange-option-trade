"""Run the targeted ETH rollout against a disposable database, never production."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.default_strategies import ETH_INSTRUMENT, ETH_TEMPLATE_SOURCES, default_strategy_definitions
from app.exit_schedule import validate_template
from scripts.shared_library import ETH_FIXED_IDS, ETH_RETIRED_NAMES, eth_catalog, seed

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_ETH_CATALOG_DATABASE_URL"), reason="Requires disposable eth_catalog_test PostgreSQL"
)


def library(cursor):
    cursor.execute(
        """select id::text,name,definition_json,version,enabled_for_ai,deleted,updated_at
           from trade.saved_strategies order by name,id"""
    )
    return cursor.fetchall()


@pytest.fixture
def catalog():
    with psycopg.connect(os.environ["TEST_ETH_CATALOG_DATABASE_URL"], row_factory=dict_row) as connection:
        assert connection.execute("select current_database() as name").fetchone()["name"] == "eth_catalog_test"
        with connection.cursor() as cursor:
            cursor.execute("create schema if not exists trade")
            cursor.execute((Path(__file__).parents[1] / "db/002_application_state.sql").read_text(encoding="utf-8"))
            definitions = default_strategy_definitions(datetime(2026, 10, 11, tzinfo=UTC))
            for item in definitions:
                definition = validate_template(item.model_dump(mode="json", exclude_none=True))
                # Production edits to BTC controls must be copied instead of defaults.
                if item.name == "Long strangle":
                    definition["stopLossPercent"] = 37
                    definition["takeProfitPercent"] = 61
                retired = "credit spread" in item.name
                cursor.execute(
                    """insert into trade.saved_strategies
                       (id,user_id,name,definition_json,enabled_for_ai,version,deleted,created_at,updated_at)
                       values (%s,null,%s,%s,true,7,%s,now(),now())""",
                    (uuid4(), item.name, Jsonb(definition), retired),
                )
                if item.name in ("Long call", "Long put", "Long ATM straddle", "Short ATM straddle"):
                    eth_name = f"ETH {item.name}"
                    eth = {**definition, "name": eth_name, "instrument": dict(ETH_INSTRUMENT),
                           "description": definition["description"].replace("BTC", "ETH")}
                    cursor.execute(
                        """insert into trade.saved_strategies
                           (id,user_id,name,definition_json,enabled_for_ai,version,created_at,updated_at)
                           values (%s,null,%s,%s,true,2,now(),now())""",
                        (ETH_FIXED_IDS[eth_name], eth_name, Jsonb(eth)),
                    )
            for status in ("active", "completed"):
                identifier = str(uuid4())
                cursor.execute(
                    """insert into trade.strategies(id,owner_id,status,created_at,data)
                       values (%s,'test-owner',%s,now(),%s)""",
                    (identifier, status, Jsonb({"id": identifier, "name": "ETH Short ATM straddle",
                                              "saved_strategy_id": ETH_FIXED_IDS["ETH Short ATM straddle"],
                                              "definition_json": eth})),
                )
            try:
                yield cursor
            finally:
                connection.rollback()


def test_rollout_copies_live_btc_rules_and_preserves_runs(catalog):
    before = library(catalog)
    btc_before = [row for row in before if not row["name"].startswith("ETH ")]
    unchanged = [row for row in before if row["name"] in ("ETH Long call", "ETH Long put")]
    catalog.execute("select id,status,data from trade.strategies order by id")
    runs = catalog.fetchall()
    assert eth_catalog(catalog, True) == 8
    after = library(catalog)
    assert [row for row in after if not row["name"].startswith("ETH ")] == btc_before
    assert [row for row in after if row["name"] in ("ETH Long call", "ETH Long put")] == unchanged
    eth = [row for row in after if row["name"].startswith("ETH ") and not row["deleted"]]
    assert {row["name"] for row in eth} == {f"ETH {name}" for name in ETH_TEMPLATE_SOURCES}
    strangle = next(row for row in eth if row["name"] == "ETH Long strangle")
    assert strangle["definition_json"]["stopLossPercent"] == 37
    assert strangle["definition_json"]["takeProfitPercent"] == 61
    for row in after:
        if row["name"] in ETH_RETIRED_NAMES:
            assert row["deleted"] and not row["enabled_for_ai"]
            assert row["definition_json"]["enabledForAi"] is False
            assert row["version"] == 3
    catalog.execute("select id,status,data from trade.strategies order by id")
    assert catalog.fetchall() == runs


def test_dry_run_does_not_mutate_templates(catalog):
    before = library(catalog)
    assert eth_catalog(catalog, False) == 8
    assert library(catalog) == before


def test_rollout_is_idempotent_and_generic_seed_does_not_revive_straddles(catalog):
    assert eth_catalog(catalog, True) == 8
    before = library(catalog)
    assert eth_catalog(catalog, True) == 0
    assert seed(catalog, True) == 0
    assert library(catalog) == before


def test_missing_btc_source_refuses_without_partial_changes(catalog):
    catalog.execute("update trade.saved_strategies set deleted=true where name='Long ITM put'")
    before = library(catalog)
    with pytest.raises(RuntimeError, match="current shared BTC source"):
        eth_catalog(catalog, True)
    assert library(catalog) == before


def test_existing_eth_drift_is_not_silently_overwritten(catalog):
    catalog.execute(
        """update trade.saved_strategies set definition_json=jsonb_set(definition_json,'{stopLossPercent}','45')
           where name='ETH Long call'"""
    )
    before = library(catalog)
    with pytest.raises(RuntimeError, match="Existing ETH rules differ"):
        eth_catalog(catalog, True)
    assert library(catalog) == before


def test_deleted_counterpart_is_not_resurrected(catalog):
    catalog.execute("update trade.saved_strategies set deleted=true where name='ETH Long put'")
    before = library(catalog)
    with pytest.raises(RuntimeError, match="explicitly retired"):
        eth_catalog(catalog, True)
    assert library(catalog) == before


@pytest.mark.parametrize("future", [False, True])
def test_only_future_retiring_straddle_proposals_block_rollout(catalog, future):
    identifier = str(uuid4())
    catalog.execute(
        """insert into trade.strategy_proposals(id,owner_id,status,created_at,activation_time,data)
           values (%s,'global','scheduled',now(),now() + %s::interval,%s)""",
        (identifier, "10 minutes" if future else "-1 day",
         Jsonb({"id": identifier, "saved_strategy_id": ETH_FIXED_IDS["ETH Short ATM straddle"]})),
    )
    before = library(catalog)
    if future:
        with pytest.raises(RuntimeError, match="straddle proposal is pending"):
            eth_catalog(catalog, True)
        assert library(catalog) == before
    else:
        assert eth_catalog(catalog, True) == 8
