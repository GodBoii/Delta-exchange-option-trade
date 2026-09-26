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

Migrations in `backend/db` run in order when the writer starts. `006_local_only.sql` removes the retired recovery outbox and gate, and adds the change-notification trigger and the `ai` schema.

## Roles

- `trade_writer` (from `deploy/local-postgres.env`) owns the database. Only the trading writer uses it.
- `AI_DATABASE_URL` role can read and write `ai.*` only. The writer creates or updates it at startup from the URL, so rotating the password in `backend/.env` and restarting the writer is enough.
- `LOCAL_READER_DATABASE_URL` role is read-only and is used by API read replicas.

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

## One-time Supabase import

`backend/scripts/import_supabase_ai.py` copies `public.analysis_reports` and the two Agno session tables from Supabase into the empty local `ai` schema. It verifies row identities before committing. Chart images are not copied, and chart references are removed from imported snapshots. Run it inside the analysis image with `SUPABASE_DB_URL` supplied only for that command.
