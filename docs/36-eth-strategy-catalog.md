# ETH strategy catalog

ETH mirrors the ten current BTC strategy shapes except Long ATM straddle and
Short ATM straddle. Its shared library contains:

| ETH strategy | Strike selection |
|---|---|
| ETH Long call | ATM |
| ETH Long put | ATM |
| ETH Long strangle | Call and put two listed steps OTM |
| ETH Short strangle | Call and put two listed steps OTM |
| ETH Short OTM put | Two listed steps OTM |
| ETH Short OTM call | Two listed steps OTM |
| ETH Long ITM call | Two listed steps ITM |
| ETH Long ITM put | Two listed steps ITM |

The single-leg call and put retain their ATM strikes. Expiry and holding duration
still use the current session resolver. Strike steps count listed contracts;
they do not imply equal dollar or percentage distances on BTC and ETH.

## Targeted database rollout

Run `python -m scripts.shared_library eth-catalog` inside the Ubuntu trading
backend image to print the plan. Add `--apply` to commit. The command copies
missing counterparts from the saved BTC definitions, including current risk
settings, rather than replacing production settings with source-code defaults.
It preserves the existing ETH Long call and ETH Long put rows and versions when
their rules match BTC. It refuses unexpected drift, duplicate source names,
missing BTC sources, explicitly retired counterparts, or a future pending
proposal for a retiring ETH straddle.

The change is one PostgreSQL transaction protected by an advisory lock and
template row locks. Six new shapes have stable IDs ending in 007 through 012.
IDs 003 and 004 remain reserved for the retired straddles. Both straddle rows are
soft-deleted, disabled, and versioned; their historical IDs are never reused.
Repeating the command does nothing. Generic seeding also leaves retired rows
retired.

The command does not update BTC templates, private strategies, run definitions,
orders, account settings, or reporting history. Existing execution guards reject
new automated entries referencing a retired template. Open trades retain their
own saved definitions and risk exits. The normal agent catalog reads current
enabled templates from PostgreSQL and filters them by asset.

The main agents are named `BTC Automation agent` and `ETH Automation agent`.
Their stable agent IDs are unchanged. ETH main analysis and activation recheck
use the shared instructions without the former ATM-only restriction or the
assertion that ETH moves more than BTC. Saved catalogue rules still determine
which strategies each agent may select.

The two credit-spread definitions remain in the canonical BTC source catalog but
are retired in this installation's database. Use the targeted `eth-catalog`
operation rather than applying unrelated catalog changes during this rollout.

Build the trading image before replacing the running writer. Deploy while no
analysis or trade execution is running, then verify the writer health endpoint,
scheduler readiness, ten current BTC templates, eight current ETH templates,
retired ETH straddles, unchanged BTC rows, and retained history. The frontend
loads the library from the backend; this change requires no frontend release.

## Validation

Template tests compare all eight ETH definitions to their BTC counterparts and
materialize every shape through the current-session expiry resolver. PostgreSQL
tests exercise the real transaction, dry-run behavior, idempotency, saved BTC
edits, run/history preservation, retired rows, and pending-proposal guards.

Run `tests/test_eth_catalog_postgres.py` with `TEST_ETH_CATALOG_DATABASE_URL`
pointing to a disposable database named `eth_catalog_test`. Its fixture rejects
other database names. Do not use the production database for tests.
