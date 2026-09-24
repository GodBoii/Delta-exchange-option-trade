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

Up to 100 Convex users are supported. Execution dispatch runs up to eight account groups concurrently and serializes aliases of the same Delta account. This does not guarantee simultaneous fills across accounts.

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

Historical data comparison and authenticated API checks do not prove 100-account
exchange throughput. That still requires a controlled execution benchmark.
The global scheduler and normal trading operation resume after deployment.

Do not flip storage flags back to Supabase. The old trading tables are gone.
Recovery requires stopping writers and reconciling new Convex activity against
the encrypted snapshot. Do not replay migrations 001–026 on the cut-over database.
