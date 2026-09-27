# Local storage

Supabase is used for login and the `profiles` row. Everything else lives in PostgreSQL 17 on the Ubuntu server. Convex is no longer used, and Supabase no longer stores reports, sessions or charts.

## Where data lives

| Data | Location | Written by |
|---|---|---|
| Login, profile (`display_name`, `avatar_url`, `phone_number`, `user_type`) | Supabase Auth and `public.profiles` | Supabase |
| Accounts, encrypted Delta credentials, capital and automation settings | `trade.users`, `trade.system_settings` | Trading writer |
| Strategy library | `trade.saved_strategies` | Trading writer |
| Runs, executions, orders, capital slots, proposals, analysis jobs | `trade.*` runtime tables | Trading writer |
| Order intents, product claims, exchange fills | `trade.order_intents`, `trade.product_claims`, `trade.exchange_fills` | Trading writer |
| Analysis reports and market snapshots | `ai.analysis_reports` | Analysis service and writer |
| Chart images (PNG) | `ai.chart_images`, deleted after `CHART_RETENTION_DAYS` (90) | Analysis service |
| Agno sessions | `ai.news_agent_sessions`, `ai.automation_agent_sessions` | Analysis service |
| Owner reporting copies of software runs, capital observations, automation audit | `owner_reporting.*` | Trading writer |

Migrations in `backend/db` run in order when the writer starts. `006_local_only.sql` removes the retired recovery outbox and gate, and adds the change-notification trigger and the `ai` schema. `007_owner_reporting.sql` adds the `owner_reporting` schema.

## Owner reporting

The owner's Users page and every account's My P&L page read from `owner_reporting`. It is a reporting copy. Nothing that places orders, reserves capital or schedules work reads it.

| Table | Holds |
|---|---|
| `user_profiles` | Allowed profile fields per Supabase user ID: name, email, phone, avatar URL, role, registration date, Delta connection status and account name, last sync time. Supabase stays authoritative. |
| `trade_ledger` | One row per software run ID: owner, strategy, dates, status, `accounting_state`, realized and gross P&L, exchange fees, premiums, capital budget and wallet at entry, and a sanitized `detail` copy of executions, orders and settlement. `deleted_by_user_at` marks runs the user removed. |
| `capital_observations` | Wallet totals, capital-policy settings and per-run allocations, each with its source (`strategy_entry`, `live_wallet`, `capital_policy`) and time. A run allocation is never labelled as the user's total capital. |
| `automation_changes` | Owner switch changes: actor, target, old and new value, time. |
| `backfill_runs` | Each historical import with its counts and whether verification passed. |

Money is `numeric(28,10)` in USD; timestamps are UTC. The INR view converts at display time only.

Never copied: passwords, Supabase tokens, Delta API keys or secrets, encrypted credential payloads, connection fingerprints, or raw exchange order responses.

### Capture and deletion

- `LocalRuntimeStore._save` refreshes the ledger row whenever a run, execution or order row changes, inside the same transaction. The refresh runs in a savepoint, so a reporting bug is logged and never blocks a trading write. Risk-display refreshes (`risk_state`, `risk_monitor_at`) do not rewrite the copy.
- Deleting a run (`DELETE /api/strategies/{id}/record`) captures a final copy and sets `deleted_by_user_at` before the run, executions and orders are removed, in one transaction. If the copy cannot be written, the deletion rolls back. The existing guards still refuse deletion of live or unresolved runs.
- Personal endpoints (`/api/me/*`) exclude deleted runs. Owner endpoints (`/api/owner/*`) include them, marked. `/api/strategies` never reads the ledger.

### Accounting rule

`classify_run` in `backend/app/run_accounting.py` is the only inclusion rule. A run is `settled`, and counts toward net P&L, wins and losses, only when it completed, every entry lot closed, no order has an unknown fill state, fees are final, contract values are known, and realized P&L exists. Other states are `open`, `scheduled` (drafts included), `cancelled`, `incomplete` and `attention`, with an `exclusion_reason`. Premium received on an open position is never counted as profit.

Personal totals exclude runs the user deleted. Owner totals include them. Both use the same rule and the same SQL aggregate.

Platform commission is not calculated. The ledger keeps the fee and fill data needed to add it later.

### Historical import

The writer runs the import at startup until a verified run exists for the current `LEDGER_VERSION`. A failure is logged and leaves `historyComplete: false` in P&L responses; trading continues, and deletion still archives on its own. Run it by hand with:

```bash
docker compose exec delta-exchange python -m scripts.backfill_owner_ledger          # import and verify
docker compose exec delta-exchange python -m scripts.backfill_owner_ledger --verify # counts only
```

It is idempotent: run IDs are the key, so reruns update rows. Verification requires every `trade.strategies` row to have a copy with the same owner, status and order count. It prints per-user source and ledger counts. The exit code is non-zero if verification fails. Only values the software recorded are imported; wallet balances that were never recorded stay missing.

### Automation switch

`set_account_automation` in `backend/app/automation.py` is used by both `PUT /api/automation/settings` and the owner's `PUT /api/owner/users/{id}/automation`. Switching off cancels scheduled analysis runs, proposals and not-yet-entered strategies for that account. Open positions keep their exit and risk rules. Shared allocation, agent scheduling and follow-ups read the switch `for share`, so a switch-off waits for an in-flight allocation and then cancels it. Agent-proposed entries re-check the switch before entering.

### Access

Every `/api/owner/*` request re-checks `user_type = 'owner'` with a profile no older than 10 seconds. Registered users come from Supabase `profiles` through the service connection, paged on the server. Live wallet reads for the owner run on selection only, at most three at a time, time out after 8 seconds and are cached for 30 seconds. A Delta failure returns `unavailable` instead of a number.

## Roles

- `trade_writer` (from `deploy/local-postgres.env`) owns the database. Only the trading writer uses it.
- `AI_DATABASE_URL` role can read and write `ai.*` only. The writer creates or updates it at startup from the URL, so rotating the password in `backend/.env` and restarting the writer is enough.
- `LOCAL_READER_DATABASE_URL` role is read-only and is used by API read replicas. It can read `trade.*`, `ai.*` and `owner_reporting.*`. Replicas skip profile snapshots and wallet observations, which only the writer records.
- The analysis role has no access to `owner_reporting`.

The analysis service never connects to `trade.*`. It schedules strategies, follow-ups and rechecks through the writer's `/internal/research` endpoint, which is authenticated with `ANALYSIS_SERVICE_SECRET`.

## Request path

- The API verifies Supabase access tokens locally with the project's published ES256 keys (`supabase_auth.py`). Legacy symmetric tokens fall back to Supabase's user endpoint. A token stays valid until it expires, at most one hour after logout.
- Profiles are cached per user for `AUTH_PROFILE_CACHE_SECONDS` (60). If Supabase is briefly unreachable, the cached profile is still served.
- Row changes to `trade.strategies` and `trade.analysis_jobs` emit `NOTIFY trade_changes`. Each API process holds one `LISTEN` connection and fans changes out to its `/api/revisions` streams. Risk-display refreshes do not notify.
- Charts are served from `GET /api/charts/{run}/{chart}` with an HMAC signature and expiry. The key is derived from `ANALYSIS_SERVICE_SECRET`, so any replica can verify a link. The model receives chart bytes directly, and sessions do not store image copies.

## Scaling

- API reads scale with `docker compose --profile read-replicas up -d`. Replicas share the database and change feed.
- One trading writer owns the scheduler and exchange orders. This keeps the journal's single-dispatch guarantee simple. Per-account work inside it runs concurrently (`EXECUTION_ACCOUNT_CONCURRENCY`).
- Connection pool size is `DATABASE_POOL_SIZE` (20) per API process. PostgreSQL allows 200 connections.
- Scheduler scans, follow-up cleanup and library pages are indexed and paginated, so per-cycle cost does not grow with every stored row.

## Backups

`trade-postgres-backup` writes a compressed `pg_dump` to `data/backups` once a day and keeps seven days. To restore into an empty database:

```bash
docker compose stop delta-exchange news-analyzer
docker compose exec -T trade-postgres pg_restore -U trade_writer -d trade_cognition --clean --if-exists < data/backups/<file>.dump
docker compose up -d
```

Reconcile open Delta positions and orders before re-enabling automation after a restore.

The dump covers the whole database, so `owner_reporting` is restored with `trade`. Ledger rows for runs deleted after the backup was taken are lost with the restore, because the source rows are gone too. After a restore, run `python -m scripts.backfill_owner_ledger --verify` and confirm it passes before relying on historical P&L. To check that a dump contains the owner tables: `pg_restore --list <file>.dump | grep owner_reporting`.

## One-time Supabase import

`backend/scripts/import_supabase_ai.py` copies `public.analysis_reports` and the two Agno session tables from Supabase into the empty local `ai` schema. It verifies row identities before committing. Chart images are not copied, and chart references are removed from imported snapshots. Run it inside the analysis image with `SUPABASE_DB_URL` supplied only for that command.
