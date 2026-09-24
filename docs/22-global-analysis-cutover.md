# Global analysis and storage cutover

Deployed September 23, 2026. This supersedes the storage instructions in documents 17–19.

## Storage

Supabase Auth owns authentication. Its public schema contains only:

- `profiles`: Auth UUID, display name, email, phone, avatar, user type, timestamps.
- `analysis_reports`: report, market evidence, member responses and tool calls, keyed by analysis ID.

The private `automation-charts` bucket stores new chart images. Agno's native session tables remain in the `ai` schema. Historical private reports retain `legacy_user_id`; new shared reports leave it null.

Convex owns:

| Table | Purpose |
| --- | --- |
| users | Supabase UUID, capital policy, automation participation, encrypted Delta credentials |
| systemSettings | Owner UUID, global analysis settings, server outbound IP |
| savedStrategies | One current record per built-in or private strategy |
| analysisJobs | Analysis timing, status, outcome and report identifiers |
| strategy_proposals | Shared decisions and account allocations |
| strategies | Scheduled, active and completed account strategy runs |
| executions | Entry and exit execution attempts |
| execution_orders | Exchange order history |
| strategy_capital_slots | Account capital reservations |
| orderIntents | Idempotency and uncertain-order recovery |
| exchangeFills | Fill-based accounting |
| productClaims | Protection against conflicting ownership of an account position |
| signals | Small realtime UI invalidations |

There is no strategy-version or strategy-edit-history table. The existing numeric revision stays on the current strategy record for concurrent-edit checks. Names do not include version suffixes. Execution records retain the definition used for that trade.

## Behavior

The scheduler creates one global job per review event. The research workflow selects one built-in strategy and one entry/exit schedule. One global recheck controls account allocation. Each enabled, connected account gets at most one allocation for that decision and sizes it using its own wallet and capital policy.

The owner can request manual analysis and publish built-ins. Manual requests reuse an existing immediate or running main analysis. Ordinary users cannot invoke that action, including by calling the API directly. Builder drafts do not automatically publish owner edits.

Global jobs have an explicit global scope rather than a synthetic Auth account. The compatibility API supplies a scope key to older internal callers. Existing private analysis history remains private and is shown alongside new shared results.

There is no configured account-count ceiling. Account settings and decision allocations
are fetched in pages, with bounded work per Convex function. Execution dispatch
serializes aliases of the same Delta account and runs independent account groups
concurrently. The default is eight groups; deployment settings can raise it after
measuring host resources and exchange latency. This does not guarantee simultaneous
fills across accounts.

## Migration evidence

Encrypted laptop backup: `data/backups/redesign-20260923T174151Z/`.
The separate recovery key and archives have restricted Windows permissions. The
backup includes PostgreSQL, Convex, deployment configuration and source rollback
material. It excludes chart image files as requested. Archive checksums and
PostgreSQL restore-directory readability were verified.

Retained:

- `prajwalghadge2005@gmail.com`, owner.
- `yadavn519@gmail.com`, user.

Field comparisons verified 197 historical reports, 30 strategy runs, 61 executions,
121 execution orders, 33 proposals, two capital slots and both credential pairs.
All 13 active built-ins came from the live Supabase catalog, replacing stale
Convex definitions. Pending private reviews and proposals were cancelled in the
destination; the new scheduler creates global reviews.

Removed three other Auth accounts and their data, one diagnostic Agno session,
983 old charts, seven empty legacy Convex tables, eleven migrated Supabase tables
and obsolete SQL functions. Supabase Vault credentials were removed only after
the encrypted Convex replacements passed decryption and live account checks.

## Verification and operating limits

- Python suite: 306 tests passed.
- Convex suite: 35 tests passed, including 100-account allocation/retry behavior.
- Production frontend build, TypeScript and ESLint passed.
- Both account APIs passed session, automation, strategy history, news history,
  capital and exchange-overview checks. Ordinary-user manual analysis returned 403.
- Both accounts returned the same next global analysis ID.
- No funded orders or paid AI analysis were triggered as tests.

At the first live 05:30 IST Asia review on September 24, the scheduler completed
one global analysis. The older per-account jobs for that time remained cancelled.
The global decision selected one strategy, and both account allocations activated
the same entry schedule at 06:00 IST. This was normal live trading, not a test order.

The later audit kept owner builder drafts local until an explicit Save built-in,
labelled older private reports in the run list, hid revision numbers beside strategy
names, and widened browser connection timeouts after a 9.3-second cold health request.
Builder recovery keys now include the signed-in UUID, so two people sharing a browser
do not load each other's unsaved drafts. An unfinished owner draft survives reload
without entering the global catalog. The first live global research context contained
no account balances or private positions.

Historical data comparison and authenticated API checks do not prove 100-account
exchange throughput. That still requires a controlled execution benchmark.
The global scheduler and normal trading operation resume after deployment.

## Scaling boundary

The original 100-account test was a test fixture, not a product limit. Settings,
due-account identity lookup, allocation and entry recheck now process users in
pages of 100. A 250-account fixture verifies multiple pages. Empty allocation
windows do not scan all users. The global analysis scheduler reads its one
system setting instead of enumerating enabled accounts on each poll.
Once a complete allocation pass succeeds, the
decision is marked complete so later polls do not reread every account. Accounts
must be enabled and connected when allocation runs after the global recheck.
Normal risk checks still run on every scheduler pass, while unchanged display
snapshots are persisted at most once per ten seconds by default. Failed account
allocations leave the pass incomplete and are retried.
The backend caches the Delta account grouping used for concurrent dispatch for
30 seconds by default. Newly seen users cause an immediate lookup. Changing an
exchange connection while a strategy is active remains blocked, so this cache
does not change ownership during a live strategy. The cache is local and
discarded on restart.

The current trading backend remains one writer. Its filesystem lock prevents two
writers sharing the Ubuntu state volume, but is not a distributed lease. API and
market-data readers may be replicated; multiple trading writers on separate hosts
must not be started until account leases with fencing are in place. Convex, Supabase
and Delta impose service and exchange limits beyond our CPU and RAM.

In a 151-second Convex log sample, 408 of 640 calls were generic record reads,
87 read credentials, 86 updated records, and four checked pending allocations.
About 359 responses were marked cached. The measured pressure was in repeated
record access, so batching entry checks and reducing unchanged risk writes is
more useful now than adding Redis. Redis would need its own persistence, failover,
invalidation rules, metrics and credentials. It would not replace Convex's
transactional order journal or account allocations. Reconsider it only after
measuring a repeated market-data or cross-process cache workload that Convex's
query cache and the current in-process caches cannot serve.

Convex's [query cache](https://docs.convex.dev/realtime) can reuse identical
query results. Its [limits](https://docs.convex.dev/production/state/limits)
still apply to each function and deployment, so pages and bounded workers are
required even when the Ubuntu machine has spare memory. The trading writer can
scale vertically. Horizontally scaling execution requires a separate deployment
step with durable account leases, fencing before order submission, and routing
manual trade requests to the account's current writer.

Do not flip storage flags back to Supabase. The old trading tables are gone.
Recovery requires stopping writers and reconciling new Convex activity against
the encrypted snapshot. Do not replay migrations 001–026 on the cut-over database.
