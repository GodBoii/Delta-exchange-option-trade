# Conversation context: Trade Cognition local storage migration

> Superseded: Convex and the recovery mirror are retired. See [Local storage](25-local-only-storage.md).

This file is a handoff for a new coding agent. Read it before changing the storage architecture, deploying to Ubuntu, or touching the Convex projects.

## User’s original goal

The user saw Convex usage approaching or exceeding the team limits and asked for a complete audit of logs, code, APIs and database usage. The measured pressure was in the Trade Cognition project:

- `runtimeRecords.select` was the largest reader.
- `runtimeRecords.update` was the largest writer.
- `signals.publish` added a large number of writes.
- The backend scheduler polled the Convex compatibility layer every two seconds.
- The Convex team database I/O allowance was later shown as `1.1 GB / 1 GB`, with Trade Cognition at about `1010.18 MB`.
- The separate `polycognition` project was explicitly excluded from this work.

The user then asked to move Trade Cognition’s live state to the Ubuntu server and its local storage, reduce routine Convex calls by about 99%, keep existing behavior, keep the system low latency, and use Convex as a delayed recovery copy. Supabase Auth, Agno PostgreSQL sessions, analysis reports, and chart storage remain unchanged.

## Decisions made

- Ubuntu PostgreSQL is the authoritative live database.
- Convex is a delayed recovery copy, not a live second writer.
- Local commits happen before exchange order submission. Convex mirroring is asynchronous and must not block trading.
- Critical events are mirrored: strategy scheduling and outcomes, order intents and outcomes, fills, claims, capital changes, credentials, and library/settings changes.
- High-frequency polling and unchanged risk display snapshots stay local.
- Convex recovery retention is intended to keep open state and 90 days of closed state. Ubuntu keeps full history.
- If Ubuntu is unavailable, the browser provides read-only recovery views. It does not place or schedule trades.
- A brief pause is required for the final copy. No unresolved exchange exposure may exist during the final cutover.
- The new Convex account/project shown by the user is intended to become the recovery destination later. It was not yet connected to the CLI when this work stopped.
- The old Convex deployment must not be deleted until the new recovery deployment is configured, the final local state is verified, and recovery is tested.

## What the audit found

The old Trade Cognition deployment was `dev/clothwala`, deployment `knowing-horse-0`, project `trade-cognition`. The earlier dashboard showed:

- Function calls around 610K, later rising to about 771K.
- Database storage around 380 MB of 512 MB at the latest check.
- Database I/O above the 1 GB team allowance.
- Trade Cognition I/O was dominated by `runtimeRecords.select` and `runtimeRecords.update`.
- Recent Convex insights showed two OCC retries on `runtimeRecords.update` against the `strategies` table.

The code causing the pressure was the generic PostgREST-to-Convex compatibility path in `backend/app/runtime_store.py` and `convex/runtimeRecords.ts`. The backend fetched pages, filtered or projected some fields in Python, and repeatedly scanned runtime tables. Active risk state was persisted periodically, and Convex signals caused browser refetches. Credentials were also read through Convex before account session reuse could help.

## Implemented architecture

### Local PostgreSQL

The Ubuntu Docker stack now has a private `trade-postgres` service using `postgres:17.11-alpine3.24`. It has:

- A persistent Docker volume named `delta-exchange_trade-postgres`.
- No published host port.
- A private `trade-data` network.
- `fsync=on`, `synchronous_commit=on`, and `full_page_writes=on`.
- A writer role and a separate read-only role.

The compose overlay is [docker-compose.local-storage.yml](../docker-compose.local-storage.yml). The environment template is [deploy/local-postgres.env.example](../deploy/local-postgres.env.example). The real Ubuntu env file is ignored by Git.

### Local schema

SQL migrations live in `backend/db/` and are applied by `backend/scripts/init_local_db.py`:

- `001_local_journal.sql`: order intents, product claims, and exchange fills.
- `002_application_state.sql`: users, system settings, saved strategies, runtime tables, indexes, and recovery outbox.
- `003_recovery_outbox.sql`: revision triggers and outbox generation in the same local transaction.
- `004_recovery_gate.sql`: a persistent restore lock that pauses trading until reconciliation.
- `005_recovery_retention.sql`: marks related order, fill, and execution records closed when a strategy settles.

The runtime tables store the existing row shape in JSON plus indexed owner, status, relation, uniqueness and time columns. This preserves callers while allowing PostgreSQL to perform due-work filtering and limits directly.

### Local transaction implementations

- [backend/app/local_journal.py](../backend/app/local_journal.py) replaces the Convex order journal. It preserves the one-dispatch-per-client-order rule, unknown outcomes, idempotent resolution, product claim exclusivity, and fill replay behavior.
- [backend/app/local_runtime.py](../backend/app/local_runtime.py) handles indexed runtime reads, writes, updates, deletion safety, capital reservations, automation claims, fixed runs and follow-up cancellation.
- [backend/app/local_application_data.py](../backend/app/local_application_data.py) handles local credentials, account overview, library reads/writes, version checks, capital settings and automation settings.
- [backend/app/local_research_operations.py](../backend/app/local_research_operations.py) handles local research context, snapshots, per-account scheduling, shared decisions, rechecks and follow-ups.
- [backend/app/materialized_definition.py](../backend/app/materialized_definition.py) preserves the Convex guard that prevents AI proposals from changing strategy-owned fields.

The compatibility layer remains available when `APPLICATION_STORAGE=convex`. Local mode requires the complete storage cutover flags and `LOCAL_DATABASE_URL`.

### Recovery mirror

[backend/app/recovery_mirror.py](../backend/app/recovery_mirror.py) reads `trade.recovery_outbox` in batches of 25 and calls `recovery:applyBatch` asynchronously. A failed Convex request leaves events pending. `/health` exposes pending count, oldest event, lag, last success and last error.

The Convex additions are [convex/recovery.ts](../convex/recovery.ts) and its schema entries in [convex/schema.ts](../convex/schema.ts). They store private recovery records plus sanitized owner-scoped views. The recovery tests cover idempotency, revision ordering, credential exclusion, and retention.

`RECOVERY_MIRROR_ENABLED=false` is currently used on Ubuntu because the replacement Convex account is not connected yet. Local PostgreSQL still records the outbox while the mirror is disabled.

### Browser and research service

- [lib/strategy-library.ts](../lib/strategy-library.ts) uses `/api/library` in local mode and falls back to sanitized Convex recovery views for read-only access when the backend is offline.
- [app/components/StrategyBuilder.tsx](../app/components/StrategyBuilder.tsx) disables saved-library writes when the backend is offline or the recovery copy is read-only.
- [app/components/RealtimeSignals.tsx](../app/components/RealtimeSignals.tsx) uses the backend revision stream in local mode instead of Convex signal subscriptions.
- [app/components/RecoveryHistory.tsx](../app/components/RecoveryHistory.tsx) shows a read-only mirrored history surface.
- `backend/automation_agent/local_client.py` calls the authenticated `/internal/research` endpoint on the Ubuntu writer.
- The research worker no longer needs to call Convex in local mode for account, library, context, schedule, recheck, follow-up or shared-decision operations.

## Migration and recovery tools

- [backend/scripts/import_convex_export.py](../backend/scripts/import_convex_export.py) imports a Convex ZIP into an empty local database and verifies every copied record, owner, relation, timestamp, unique key, fill, claim and order intent. It validates encrypted account credentials with `CONVEX_CREDENTIAL_KEY` when available.
- [backend/scripts/rehearse_local_import.py](../backend/scripts/rehearse_local_import.py) imports into a database named `trade_cognition_stage...` only. It refuses a production URL and was used for the Ubuntu rehearsal.
- [backend/scripts/restore_local_from_recovery.py](../backend/scripts/restore_local_from_recovery.py) restores a recovery export into an empty database, enables `trade.recovery_gate.pending`, and never resumes trading automatically.
- [backend/scripts/clear_recovery_gate.py](../backend/scripts/clear_recovery_gate.py) clears that gate only when the operator explicitly supplies both manifest review and Delta reconciliation flags.
- [backend/scripts/create_local_reader.py](../backend/scripts/create_local_reader.py) creates the read-only PostgreSQL role.
- [docs/23-local-storage-cutover.md](23-local-storage-cutover.md) is the full runbook.

## Verification already completed

Local Docker PostgreSQL 17 tests passed:

- Order identity and duplicate-dispatch protection.
- Idempotent outcome resolution and fill replay.
- Product claim exclusivity and rollback.
- Indexed runtime reads and risk-only update suppression.
- Capital reservations and automation claims.
- Library version checks and credential revocation.
- Shared allocation, recheck, scheduling and follow-up transactions.
- Recovery mirror failure/retry behavior.
- Convex export import into an empty database.
- Recovery restore lockout.
- Authenticated local library and private research endpoint.

Latest checks:

- Backend: 347 tests passed.
- Convex/Vitest: 41 tests passed.
- TypeScript typecheck passed.
- ESLint passed.
- Ruff passed.
- Production Next build passed.
- Both backend Docker images built locally and on Ubuntu.
- The isolated Docker PostgreSQL database imported and verified 643 records from the first rehearsal snapshot.

## Ubuntu state now

Ubuntu server: `ssh.tradecognition.online`, user `arun`, repository `/home/arun/apps/delta-exchange`.

The checkout is on branch `codex/local-postgres-cutover` at commit `83a06eb`. PostgreSQL 17 is healthy in `delta-exchange-trade-postgres-1`. The final snapshot was copied to the restricted path:

`/home/arun/apps/delta-exchange/data/cutover/trade-cognition-final-20260926.zip`

The final snapshot SHA-256 is:

`3f2328af7203176a97e04a8d46ea780957b6f9416d44c4f72d4c27ecebc8b10d`

The final snapshot contained 653 records:

| Table | Records |
|---|---:|
| users | 2 |
| systemSettings | 1 |
| savedStrategies | 17 |
| strategies | 42 |
| executions | 81 |
| execution_orders | 157 |
| strategy_capital_slots | 4 |
| strategy_proposals | 51 |
| analysisJobs | 221 |
| orderIntents | 36 |
| exchangeFills | 41 |
| productClaims | 0 |

The final cutover conditions were checked against the old Convex deployment and Delta:

- Scheduled strategies: 0.
- Active strategies: 0.
- Executing-entry strategies: 0.
- Executing-exit strategies: 0.
- Attention strategies: 0.
- Product claims: 0.
- Running analyses: 0.
- Unresolved order intents: 0.
- Both connected Delta accounts: 0 positions and 0 open orders.

The final import was applied to Ubuntu’s production `trade_cognition` database. The backend was rebuilt and restarted in local PostgreSQL mode. Current Ubuntu health reported:

- Scheduler enabled and running.
- Two connected private Delta streams.
- One public mark stream and two symbols.
- `recoveryPending=false`.
- `recoveryMirrorEnabled=false` because the new Convex account is not configured yet.

The old Convex library writes were frozen with `CONVEX_LIBRARY_WRITES_PAUSED=true` before the final export. Do not delete the old deployment yet.

## New Convex destination status

The user supplied a screenshot of a new account/project:

- Team/account: `prajwal-ghadge`.
- Project: `Trade-Cognition`.
- Deployment: `wonderful-lobster-242`.

The CLI session currently has access only to the old `clothwala` team. The browser was signed out while trying to inspect the new account, so the new deployment has not been deployed or seeded. The next Convex phase is:

1. Sign into the new Convex account in the browser.
2. Confirm the project and deployment URL/access.
3. Deploy `convex/schema.ts` and `convex/recovery.ts` to the new destination.
4. Seed recovery data from the local PostgreSQL outbox/current state, not from the over-quota old deployment.
5. Set the new Convex service secret and recovery URL in Ubuntu.
6. Set `RECOVERY_MIRROR_ENABLED=true`, restart, and verify `recoveryMirror.pending=0` and `lagAlert=false`.
7. Only after recovery is verified should the old `knowing-horse-0` deployment be considered for deletion.

## Git state and unrelated work

The migration is on `codex/local-postgres-cutover`, pushed to:

`https://github.com/GodBoii/Delta-exchange-option-trade/tree/codex/local-postgres-cutover`

The branch contains 57 migration commits, each changing exactly one file, as requested. The following files remain uncommitted in the worktree and are unrelated to the local-storage cutover. Preserve them; do not reset or delete them:

- `backend/app/default_strategies.py`
- `backend/automation_agent/team.py`
- the remaining description-related changes in `backend/automation_agent/tools.py`
- `backend/tests/test_automation_agent.py`
- `backend/scripts/sync_default_strategy_descriptions.py`

The local-storage changes in `backend/automation_agent/tools.py` were committed separately. The working-tree remainder in that file is the unrelated strategy-description work.

## Important safety rules for the next agent

- Do not delete the old Convex deployment while the new recovery destination is unconfigured.
- Do not re-enable old Convex library writes. The old deployment is frozen.
- Do not flip `RECOVERY_MIRROR_ENABLED=true` until the new account credentials are installed in Ubuntu.
- Do not run a second trading writer.
- Do not clear `trade.recovery_gate` unless a restore was performed and both the manifest and Delta exposure were reconciled.
- Do not put any secret, credential key, deploy key or database password into Git, this document, chat, or Vercel public variables.
- If Ubuntu is restarted in local mode, check `/health` before enabling automation. The expected signal is `recoveryPending=false` and a healthy scheduler.
