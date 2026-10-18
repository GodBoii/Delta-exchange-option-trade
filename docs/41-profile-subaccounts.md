# Profile subaccounts

Profile has a Sub accounts entry that opens account management and server IP setup
in the workspace. See [Sub account interface](42-subaccount-interface.md) for the layout.
Existing users retain main-account trading until they change the management setting.

## Setup and controls

1. Create subaccounts and their API keys on Delta. The documented public API does not create them.
2. Open Profile, then Sub accounts. Discovery uses the connected main key and shows accounts even before child keys are added.
3. Connect each child key. The backend verifies its account ID against current parent discovery and probes wallet,
   positions and orders without placing an order. Secrets remain encrypted in PostgreSQL and are never returned.
4. Choose each child's participation: disabled, manual only, or automatic.
5. Choose main-account trading, automatic subaccount assignment, or manual assignment for new runs.
6. Enable automatic funding separately if desired. It is off by default.

Manual assignment selects an unentered run under Assign a run in an expanded account. Assignment is permanent.
Cancel the run if it needs a different account. An assigned run retains its account when management settings change.

Each expanded account shows wallet balances, recorded software realized P&L, positions and working orders.
Positions can be closed and orders cancelled through that account's credentials. History includes account labels
and an account filter. Existing personal and owner P&L totals already include all software runs for their owner.
Internal capital transfers do not enter the trade ledger as profit or new capital.

## Execution and capital

The account registry and assignments are additive tables in migration `012_exchange_accounts.sql`.
The original `trade.users.connection` remains the main credential source. No existing credentials or historical
runs are moved. New child assignments also stamp the strategy JSON with its exchange account ID and name.

Automatic selection reserves one enabled, connected, exchange-flat subaccount per run. A partial unique index
prevents concurrent reservation of the same Delta account, including aliases using another application login.
The persisted account identity drives entry, risk monitoring, scheduled exits and restart reconciliation.
A missing child key never redirects a run to the main account.

Percentages apply once to the sum of the main wallet and participating child wallets. Active assigned children
remain in that total even when disabled for future entries. Existing BTC/ETH budgets, overall limits,
concurrency settings and capital reservations still apply. Sizing on the child uses the resulting fixed budget.
Budget and pool-total snapshots cannot be changed after funding begins. Policy edits never withdraw margin from
an active account. Read failures make pool capital unavailable rather than silently omitting an account.

Automatic funding tops up the selected child from the main account and returns idle surplus above its budget.
If needed, it first reclaims available funds
from other enabled, unassigned, exchange-flat children. Outgoing transfers require no positions, working orders,
uncertain order intents or source-account reservation. The only reserved-source exception is the selected run's
own pre-entry surplus, before any order is submitted. The source must have enough available USD.

Transfers are journaled before dispatch and serialized across the parent family. Repeated request IDs return the
original outcome. A timeout, server error or interrupted submission blocks further family funding and entries.
Reconcile reads paginated Delta transfer history and requires one matching transfer. Missing or ambiguous evidence
leaves the transfer blocked; it never causes another POST. Delta documents no transfer idempotency token.

Finished accounts are released only after the exchange is flat and pending transfers are resolved. Cleanup runs
separately from trading so a slow wallet lookup cannot delay risk checks. Disabling a child stops future entries
but preserves its exit credentials. Removing a busy key or disconnecting the main connection with pending child
runs/transfers is rejected. Credential rotation invalidates the cached account sessions.

Observed margin modes are displayed as returned by Delta. This feature does not change them. The live India
response includes `cross`, which differs from the modes enumerated by the public margin-change Swagger schema.

## Server IP

The trading writer refreshes its outbound IP from ipify on startup and every five minutes. Sub accounts > API setup shows the
persisted address with Copy, Refresh and the last-check time. It labels data older than ten minutes stale.
Failed refreshes retain the previous value. Read replicas report the writer's persisted address and reject
refresh mutations. The browser's IP, Vercel address and incoming proxy headers are not used.

## Rollout and verification

Deploy the backend before the frontend. Writer startup applies migration 012 through the normal migration runner.
Leave users in main mode until they connect and enable their child keys. No automatic funding or account-mode
setting is enabled by migration.

Tests cover parent membership, credential isolation, concurrent reservations, immutable account/budget identity,
pool totals, funding defaults, transfer timeouts and replay prevention, idle-capital reuse, history reconciliation,
key rotation, disabled-entry behavior, flat verification and authenticated Profile endpoints.
PostgreSQL tests require an isolated `TEST_LOCAL_DATABASE_URL`. Never run them against the trading database.

The history endpoint accepts signed query parameters. Its documented GET body receives HTTP 403 on the live
India gateway; the client uses query parameters, verified by read-only requests on both connected main accounts.

Browser verification used the real Profile component with temporary fixture data at 390px and 1440px widths.
Checks covered IP copying, management saves, account expansion, dummy-key submission and clearing, balances,
positions/orders, transfer amount validation and horizontal overflow. The temporary verification route was removed.
Live verification was limited to subaccount discovery and transfer history; no live settings, orders or funds were changed.
