# Implementation checklist and release boundary

September 16 update: [shared-analysis verification](19-shared-analysis-verification.md) supersedes the shared-scheduling status below. One common strategy, shared recheck and per-account sizing are now connected and tested. Production storage cutover remains pending.

This replaces the earlier progress summaries. It distinguishes code, deployed Convex functions, migrated data and running production behaviour. Passing a unit test is not a production cutover.

## What you asked for

The latest catalog rule replaces the earlier custom-strategy matching proposal. The project owners maintain the built-in strategies. The shared AI analysis sees only that catalog, and users see the same built-in strategies. A user's allocated budget determines their order size; private saved copies must not override a ranked built-in strategy. Shared scheduling, recheck, account allocation and the catalog UI still need integration and end-to-end verification before this rule is deployable.

- Explain the September 13 loss, each stop type, the architecture and technical terms.
- Re-evaluate the audit before implementation and preserve existing workflows.
- Correct accounting, ownership, partial fills, failed exits and restart recovery.
- Reduce latency and duplicate work without compressing away safeguards.
- Use Convex for application data while retaining Supabase Auth and native Agno PostgreSQL session/run storage.
- Put Supabase changes in new SQL files for you to execute.
- Complete code and verification; you will push GitHub and rebuild/restart Ubuntu, with Vercel rebuilding the frontend.

## Evidence and work completed

| Work | Status and evidence |
| --- | --- |
| Incident diagnosis | Exchange fills and wallet records reconcile to the earlier documented loss. The educational report and findings remain under `data/`. |
| SQL 019 and 020 | You ran them. Your screenshots showed revoked live-client writes and removed library foreign keys. |
| Library import | 15 definitions imported to Convex and verified field-for-field; the source was reread to confirm no concurrent change. Import mode was then disabled. |
| Native Agno and login | Kept intact. Runtime job metadata is distinct from Agno's native session tables. AI chart artifacts remain with the AI storage path. |
| Connection/account storage | Convex tables and encrypted credential adapter implemented, with identity-binding, revocation and separate research access tests. Final source import awaits stopped writers. |
| Runtime records | Indexed Convex record storage preserves existing API record shapes; large reports/snapshots are separated from scheduling metadata. Claims and multi-record actions are dedicated atomic mutations. |
| AI scheduling | Convex selection, proposal/recheck creation, drop/reconfirm and follow-up paths implemented. Research cannot read credential ciphertext or change saved risk fields. |
| Ownership and fills | Exclusive new-run claims, account-scoped fill deduplication, remaining-size accounting and late-commission handling implemented. Legacy flat reporting uses proven complete intervals. |
| Execution recovery | Durable submission identity/context, no blind resend, record reconstruction, interrupted-state recovery and partial-entry unwind implemented for a single trading writer. |
| Stops and sizing | Emergency toggle, missing marks, unsupported controls, duplicate contracts/IDs, original combined risk basis, maximum-expiry-loss math, unequal lots and strike-distance validation corrected. |
| Safer closes | Shorts close before hedges. Working exits prevent duplicate closes. Manual account close preserves protective orders if closing fails. |
| Performance | Reusable account HTTP connections, separated request capacity, quota reset handling, incremental persistence, complete query pagination, event wakeups and fresh mark caching implemented. |
| Market service | Bounded cache, shared identical requests, concurrent unrelated reads, historical candle bounds and depth-specific freshness corrected. |
| Tests and tools | Local pytest, Convex tests, type/lint/build checks, network-disabled Docker tests and disposable PostgreSQL permission/reference tests used. Final counts belong in the release verification section below. |

## Corrections to the earlier reasoning

The initial sub-100-ms dispatch target was not supported by measurements. A trivial Convex read from this laptop had a warm median near 329 ms. The Ubuntu route and durable mutations need separate measurement. Exchange mark updates are documented at a two-second cadence; changing the transport does not turn them into a 100-ms price source.

The early plan also understated the migration boundary. There were direct SQL writers in the AI tools, not just a backend database client. Those paths now have Convex alternatives. The old paths remain behind explicit cutover flags for rollback compatibility, not as automatic fallback after a Convex failure.

I originally proposed avoiding a compatibility adapter entirely. The implementation instead keeps a narrow backend record adapter to preserve API shapes and existing callers. It supports only the query operators the application uses, uses indexed identity/owner/status/relation scans, and rechecks update predicates inside the mutation. It does not parse SQL. Capital claims, review claims, proposal creation and follow-ups use explicit transactional functions. This is a deliberate migration compromise, not a claim that arbitrary SQL behaviour is reproduced.

The system cannot guarantee a fill when there is no liquidity, nor execute through total exchange/host failure. Unknown outcomes retain state and require reconciliation. Exchange-hosted stops remain independent. The deployment supports one trading writer; it is not an active-active cluster.

## Remaining implementation and verification before handoff

- [ ] Run the complete backend and market suites after the final changes, including Docker.
- [ ] Run Convex domain/transaction tests, application type checking, lint and production build.
- [ ] Verify migration import/readback with representative large records and preserve all source fields.
- [ ] Deploy the final additive Convex functions and verify their read endpoints and access boundaries.
- [ ] Finish the deployment guide with exact environment variables, SQL order and a stopped-writer cutover sequence.
- [ ] Check that no generated secrets, private exports or test database data enter Git.

## Actions that remain yours at release

1. Configure Vercel's library build switch and the deployment URL before the intended GitHub deployment. The library data is already present, but the backend still needs its matching cutover.
2. Stop the old backend/research writers for the final export. Do not import a live-changing execution snapshot.
3. Export/import/verify capital, profiles, encrypted connections and runtime records with the supplied scripts. Use the same encryption key on the destination backend.
4. Run SQL 021 yourself at the documented cutover point. Do not run test fixtures against Supabase.
5. Start the rebuilt services with all storage switches consistently enabled, then verify health, account access, library, capital, scheduling and historical reconciliation.

These release actions are not evidence that code is missing. They are deliberately separate because you reserved GitHub deployment, production restart and Supabase SQL execution for yourself. No production trades are part of verification.
