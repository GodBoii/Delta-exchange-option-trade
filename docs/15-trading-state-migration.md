# Trading state migration and execution corrections

Updated September 15, 2026. Target: Convex for application state, Supabase Auth for existing accounts, and native Agno PostgreSQL storage for agent sessions/runs. Exchange execution stays on the Ubuntu backend using its whitelisted egress. No production data has been migrated or deleted by this change.

## Documentation findings

Delta India documents order lookup by client ID and separate private account streams. A lost submission response must lead to lookup, not another POST. Current private authentication uses `key-auth`. Public market data uses the dedicated public socket. The repository snapshot contains older examples, so implementations must follow the current endpoint-specific sections. [Delta API reference](https://docs.delta.exchange/).

Market and stop-market orders can leave an unfilled limit remainder at an allowed trading band. Neither submission nor an acknowledgement proves closure. A cancellation-only deadman switch can remove protective orders and is not enabled by this implementation. [Delta trading bands](https://guides.delta.exchange/delta-exchange-india-user-guide/exchange-sop-and-policies/allowed-trading-bands).

Convex mutations are database transactions. External API calls are actions outside those transactions. Moving records to Convex does not make a Delta order transactional with the database. We therefore retain an explicit unknown submission state. [Convex functions](https://docs.convex.dev/functions/overview), [external calls](https://docs.convex.dev/tutorial/actions).

Agno supports PostgreSQL session storage directly. This project already uses `PostgresDb` and pins Agno 2.8.7. Keep that implementation intact; do not apply the latest documentation's schema assumptions to existing session tables without a separate version migration. [Agno PostgreSQL storage](https://docs.agno.com/reference/storage/postgres).

## Implemented in this increment

- Invalid/missing/nonfinite position quantities fail as unknown, not flat. Incomplete order snapshots and repeating pagination cursors also fail explicitly.
- Invalid short-option marks block entry before any order. Unsupported executable controls, conflicting stop fields, duplicate legs/contracts and inconsistent instruments are rejected at execution, while historical definitions remain readable.
- Emergency enable/disable controls actual bracket submission. Enabled emergency protection without its threshold is rejected.
- Missing required AI rechecks remain pending. Ordinary manual strategies retain their current readiness path.
- Risk readiness checks expected leg IDs, not only the rows that happened to exist.
- The exit path no longer waits for a reporting-only ticker request before sending the close.
- Fill reconciliation skips unchanged database writes and can look up an unknown entry by its original client ID.
- Unconfirmed entry submissions retain capital. Only an explicit exchange rejection can classify that attempt as rejected and release an otherwise unused reservation.
- The trading container no longer depends on research-service health to start. New AI runs still use existing readiness checks.
- A Convex order-intent journal stores immutable submission identity and accepted/rejected/unknown outcomes. It is wired to engine-created Delta clients behind an explicit deployment setting.
- Convex product claims prevent two new journal-enabled strategies from owning the same contract. Claims are scoped to the actual Delta account, not merely the application login. A flat account baseline is checked before entry.
- For these newly claimed runs, ordinary closing fills are allocated only when opening activity is exclusive. Conflicting external activity produces an ownership error. Legacy runs are not silently assigned this stronger ownership guarantee.
- Combined calculations retain entry credit/debit and include realized closed quantities. Closed legs need no live quote. Complete square-off reacts to the first confirmed closing fill, including a manual partial reduction, before fetching another price. Partial square-off retains the configured policy on the remaining quantity.
- New owned runs retain confirmed fill facts in Convex. Fill identity is account-scoped; repeated facts do not add quantity, and delayed commissions can complete missing fee data. Conflicting facts fail visibly.
- Owned-run finalization reconciles ordinary exits and keeps accounting-pending runs distinguishable from open exposure. Run detail does not overwrite this final result with the old entry-only summary.
- Owned exits use remaining allocated quantities, defer to an existing working reduce-only exit, and retry Attention runs with a saved exit request on the reconciliation schedule. Shorts close before bought hedges; a short-close failure stops the hedge phase.
- Active and scheduled strategy queries traverse all pages. Due exits and current risks run before new entries in each cycle.
- Defined-maximum-loss stops use a piecewise option-payoff bound, not entry credit. Mixed expiries and uncovered call ratios are rejected. Manual sizing budgets each leg's quantity individually. Unavailable strike distances are rejected instead of silently clamped.
- Legacy flat runs can finalize ordinary closing fills when a complete zero-sum interval proves exclusive allocation. Conflicting or incomplete intervals stay unresolved. This does not grant legacy open runs the new entry-time ownership guarantee.

## Journal semantics and deployment gate

`orderIntents:begin` atomically stores an unknown intent and grants dispatch to its first caller. A repeated identity cannot grant dispatch again. Changing its payload is rejected. Unknown retries perform Delta client-ID lookup; even not-found does not authorize a new POST. An accepted response is retained for repeated reads. These are submission outcomes, not fill or closure states.

The payload is canonical JSON text so the exact submitted order can be compared without losing decimal strings. It contains order parameters, not exchange API credentials. Every journal endpoint requires a dedicated backend service secret; a browser login cannot authorize these writes or read another account's journal.

The journal setting defaults off to keep an undeployed Convex schema from breaking the current backend. Once enabled, a journal error fails the operation; it never silently falls back to unjournalled submission.

Do not enable this on unattended production yet. Deployment verification and legacy backfill remain incomplete. Journal context now records strategy, execution and leg ownership so interrupted order records can be reconstructed without resubmitting them. The scheduler recovers interrupted states while excluding locally running operations. Partially submitted entries request an unwind. Unknown outcomes remain unresolved until an authoritative lookup succeeds; a missing lookup is not a definite rejection. Moving-average fill accounting handles entry and exit interleaving. Product claims are released only after confirming no executable exposure. These mechanisms require one trading writer; they do not fence an old process on another machine.

Required deployment configuration after the recovery/migration gate is satisfied:

```text
# Same dedicated secret on the Convex deployment and Ubuntu backend.
CONVEX_TRADING_SECRET=<generated service secret>
# Backend only. Use the selected deployment's existing URL.
CONVEX_URL=https://<deployment>.convex.cloud
CONVEX_ORDER_JOURNAL_ENABLED=true
```

The separate signal secret remains unchanged. Never expose the trading secret through a NEXT_PUBLIC environment variable. An outage of the configured journal blocks new order dispatch; existing exchange-hosted brackets remain independent. Backend-managed combined exits are not promised to operate through a journal outage.

## Supabase changes for manual execution

Run `supabase/migrations/019_restrict_live_strategy_client_writes.sql` manually after reviewing it. It removes direct browser mutations of live strategy rows and unnecessary broad table privileges. The backend service role and saved-definition editing remain available. Verification queries are included. This file does not remove historical tables or Agno data.

Do not delete the old application tables during this increment. They still serve production queries, saved-strategy editing, scheduling, capital allocation, reporting and SQL-backed AI tools.

## Remaining migration work

| Domain | Current authority | Required cutover work |
| --- | --- | --- |
| Login | Supabase Auth | Retain existing accounts and Convex JWT validation. |
| Native AI sessions/runs | Agno PostgreSQL | Retain pinned adapter and existing tables. |
| Saved definitions and capital settings | Convex when library switch is enabled | Import, verify, execute SQL 020 manually, and switch browser/backend/AI tools together. |
| Profiles | Supabase | Still requires a separate domain migration. |
| Strategies, executions and capital slots | Supabase | Atomic Convex claims and ownership, no-loss backfill, writer drain and reconciled cutover. |
| Exchange credential storage | Supabase credential RPC | Encrypted server-only storage and key lifecycle; never copy plaintext secrets into browser-readable documents. |
| Proposals and application scheduling metadata | Supabase SQL tools/RPCs | Remove cross-database transaction assumptions and port the actual scheduling commands. Native Agno sessions are a different domain. |
| Order submission intents and product claims | Convex when enabled | Recovery is implemented for the single-writer path; deployment and cross-process failure tests remain. |
| Ordinary fills | Convex for newly claimed runs | Incremental ingestion, legacy ownership migration and continued account reconciliation. |
| Strategy accounting | Python with stored run summary | Exclusive ordinary-fill accounting is implemented for newly claimed runs. Shared/legacy allocation remains explicit migration work. |

A generic PostgREST-to-Convex query emulator would preserve the old coupling and encourage collection scans. The migration should use explicit indexed domain operations instead. Each domain moves with all its writers and readers, rather than maintaining two writable copies.

## Verification

Run backend pytest, Binance pytest, `npm run test:convex`, `npm run typecheck`, `npm run lint` and the production build. Convex tests use its documented `convex-test` backend simulation. They verify mutation logic, not production latency, regional availability or real concurrent deployment behaviour. Actual staging tests remain required before the cutover.

Legacy history remains unchanged in production until the backend is deployed and reconciliation runs. Legacy open/shared positions still require a verified migration. No live orders or Supabase SQL were executed.

Local and Docker validation: 252 backend tests passed, including a network-disabled Linux container run. Separately, 19 Binance tests and 12 Convex/browser-adapter tests passed. TypeScript checking, ESLint and the Next.js production build passed. Compatible package updates resolved the npm audit findings; the resulting audit reported zero vulnerabilities. The installed Next.js version is 15.5.25.

Convex access was restored on September 15. The user ran codegen successfully and local bindings included the new modules, but a deployed-function inspection still listed only the two signals functions. After adding `convex/tsconfig.json`, `npx convex dev --once --typecheck enable` deployed the additive schema and functions to `knowing-horse-0`. A dedicated trading service secret was configured there and in the ignored local `backend/.env`, without displaying its value. Library, capital and journal service reads succeeded; an invalid secret was rejected. No import or trading mutation was performed during verification. The operational feature flags remain off. Propagate the service secret securely to the Ubuntu backend when staging the cutover; it is not part of Git.

The selected development deployment is also used by the existing application signals and is hosted in US East. A ten-request unauthenticated signals-query probe from the local Windows host measured 328.69 ms warm median and 640.74 ms warm maximum across nine warm requests, with a 1793.55 ms first request. These are connectivity observations for a trivial query, not Ubuntu measurements, transaction benchmarks or p99 guarantees. Remote durable writes cannot be assumed to meet the earlier sub-100-ms dispatch objective. Keep quote arithmetic local and measure the actual Ubuntu path before selecting deployment latency targets.

## Library and capital cutover

The browser uses authenticated Convex library mutations while the Python backend and Agno selection tools use named service operations. The switch defaults off. There is no fallback to Supabase after a Convex failure. Default strategies are read-only to users, saves check the expected version, deletion retires the reusable definition, and historical execution snapshots remain intact. The native Agno PostgreSQL adapter has not changed.

Perform the cutover during a paused editing/scheduling window, after Convex project access and staging tests are available:

1. Deploy the Convex functions to the intended project and set its dedicated service secret. Temporarily set `CONVEX_IMPORT_ENABLED=true` there.
2. Pause source library/capital edits and new AI scheduling. Export a fresh snapshot after the pause. The script reads tables through the service API, writes no SQL, excludes credentials, preserves IDs and versions, and checksums the export. The export is private application data; keep it under the ignored `data/` directory.
3. From `backend`, run these commands with the tested interpreter and the configured destination. The import command requires an explicit paused-source flag and refuses to overwrite conflicting destination records.

```text
python -m scripts.migrate_convex_library export ../data/library-cutover.json
python -m scripts.migrate_convex_library import ../data/library-cutover.json --source-paused
python -m scripts.migrate_convex_library verify ../data/library-cutover.json
```

4. Run `supabase/migrations/020_convex_library_reference_boundary.sql` yourself. It removes the two foreign keys pointing to the retired library and prevents browser writes to that old copy. It does not delete tables, runs, proposals, sessions or stored definitions.
5. Set `CONVEX_LIBRARY_ENABLED=true` on both Python services. Build the frontend with `NEXT_PUBLIC_CONVEX_LIBRARY_ENABLED=true` and the intended `NEXT_PUBLIC_CONVEX_URL`. Existing Supabase Auth remains the identity provider.
6. Turn `CONVEX_IMPORT_ENABLED` off. Verify reading, saving a custom strategy, stale-edit rejection, AI library selection, capital settings and scheduling before resuming automation.

Do not roll back by merely flipping the flags after new Convex edits. Those edits will not exist in the old PostgreSQL copy. Reconcile/export the changed library and restore references through a reviewed reverse migration first. The old seeding script refuses to modify PostgreSQL while the Convex library switch is enabled.

## Docker and browser verification

`backend/Dockerfile.test` includes test dependencies and excludes environment files through its dedicated ignore file. It runs pytest, not the trading server. Run it without network access and without production environment files:

```text
docker build -f backend/Dockerfile.test -t delta-exchange-audit-tests backend
docker run --rm --network none delta-exchange-audit-tests
```

Both `SCHEDULER_ENABLED` and `AUTOMATION_SCHEDULER_ENABLED` are false in the test image. The latter is a separate opt-out for maintenance/test processes and defaults true in normal deployments. A container API smoke test checked health and rejection of an unauthenticated execution request using dummy credentials and no network.

The SQL files were executed against a disposable PostgreSQL 17 container with synthetic tables and roles. Assertions verified that live client writes were revoked, backend/history access survived, library writes remained until SQL 020, both old references were removed, and historical rows remained. This validates those SQL operations, not a full recreation of Supabase Vault or production row policies. The test container was removed. `backend/tests/migration_permissions.sql` is a fixture for an empty disposable database only; never run that fixture against Supabase.

The production browser was inspected read-only. It still showed the September 13 run as Attention with two entry orders and missing settlement P&L. That verifies the deployed baseline, not this unreleased code. The local production build loaded the existing signed-in library and its dropdown without console errors; trading remained disabled because the backend was unavailable. No production save, scheduling, close or delete control was used.
