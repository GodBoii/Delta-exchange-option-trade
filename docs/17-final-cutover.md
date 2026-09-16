# Final cutover commands

September 16 update: SQL 021 was already verified as recorded in [SQL verification](18-sql-cutover-verification.md). Do not treat the later instruction to execute it as a pending action. See [shared-analysis verification](19-shared-analysis-verification.md) for the tested behavior and deployment boundary. Drain or cancel old per-user scheduled reviews/proposals before switching to shared analysis. Keep `SHARED_ANALYSIS_ENABLED=true` on the backend for the new flow.

This is the deployment sequence for the code in this checkout. The implementation checklist is in `docs/16-implementation-checklist.md`. Do not use older progress messages as the release procedure.

## What is already in place

- You applied Supabase SQL 019 and 020.
- The 15 saved strategies have been imported and verified in Convex.
- Convex functions and schema are additive. The existing signals table is retained.
- Local `backend/.env` contains the dedicated trading secret, research secret, credential-encryption key and analysis-service secret. These are ignored by Git and are never copied by Docker build.
- Native Agno session/run data remains in PostgreSQL. Existing login remains Supabase Auth.

## Before pushing

Set these build-time variables in the Vercel project before the deployment that switches the library:

```text
NEXT_PUBLIC_CONVEX_URL=https://knowing-horse-0.convex.cloud
NEXT_PUBLIC_CONVEX_LIBRARY_ENABLED=true
```

The library contains the verified definitions, so the new frontend can use it. Coordinate this deployment with the backend cutover. Do not edit library or capital settings during the final export/import window.

Securely copy the four new secret values from local `backend/.env` into the Ubuntu `backend/.env`. Do not paste secrets into Git, chat or public Vercel variables. `deploy/convex-cutover.env.example` lists the required names and flags. The encryption key must be the same key used to export encrypted credentials.

## Stop writers and build

On Ubuntu, from the repository directory, stop the application writers before making the final snapshot. This does not send any exchange orders. Existing exchange-hosted protection remains at Delta; perform the cutover with no unresolved live exposure.

```bash
docker compose stop delta-exchange news-analyzer
git pull
docker compose build delta-exchange news-analyzer
mkdir -p data/cutover
```

Set the new storage flags in `backend/.env` as shown in the example, but leave the services stopped. The one-off commands below run migration scripts, not schedulers. They read the old tables explicitly for export, even when the new runtime flag is enabled.

From your authenticated local project directory, temporarily enable the import gate:

```text
npx convex env set CONVEX_IMPORT_ENABLED true
```

## Export, import and verify

Use new filenames for a new cutover attempt; export refuses to overwrite an existing evidence file. Run from the Ubuntu repository directory. The mount stores exports on the host so they survive the one-off container.

```bash
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_library export /cutover/library.json
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_library import /cutover/library.json --source-paused
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_library verify /cutover/library.json

docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_accounts export /cutover/accounts.json
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_accounts import /cutover/accounts.json --source-paused
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_accounts verify /cutover/accounts.json

docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_runtime export /cutover/runtime.json
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_runtime import /cutover/runtime.json --source-paused
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/data/cutover:/cutover" delta-exchange python -m scripts.migrate_convex_runtime verify /cutover/runtime.json
```

Stop on any verification mismatch. Imports reject conflicting existing records instead of overwriting them. Exports are private application data. Account exports contain encrypted API credentials, not plaintext secrets. Keep the encryption key backed up separately.

After every verification passes, run `supabase/migrations/021_convex_runtime_cutover.sql` yourself in Supabase. It retains the old data and native Agno storage. It does not delete tables.

Disable imports from the authenticated local project:

```text
npx convex env set CONVEX_IMPORT_ENABLED false
```

## Start the new version

Confirm these backend flags are true on both services through the shared backend environment file:

```text
CONVEX_LIBRARY_ENABLED=true
CONVEX_ACCOUNTS_ENABLED=true
CONVEX_RUNTIME_ENABLED=true
CONVEX_ORDER_JOURNAL_ENABLED=true
DELTA_EVENTS_ENABLED=true
```

The research service receives its restricted research secret and analysis-service secret. Compose explicitly blanks the trading secret and encryption key in that container. It retains PostgreSQL access for native Agno sessions and the existing AI chart storage credentials.

```bash
docker compose up -d delta-exchange news-analyzer binace
docker compose ps
docker compose logs --tail=100 delta-exchange news-analyzer
```

Check account connection, strategy library, capital settings, historical run details, pending AI reviews and health before resuming unattended use. The private stream authenticates from Ubuntu's whitelisted IP. REST reconciliation remains active after reconnects and when mark streams are stale.

The deployment must have one trading writer. Do not keep an older backend running against the same exchange account. Docker Desktop verification containers used in this task had no network or production credentials.

## Rollback boundary

Do not merely flip flags back after new records or fills have been written to Convex. The retained PostgreSQL data would be stale. Stop writers, export the changed Convex records and reconcile fills before a reviewed reverse migration. Keep the exchange-protection state and unknown order intents visible throughout recovery.

The old `ai` tables, Supabase Auth and historical source tables are intentionally retained. Deleting them is not part of this release.
