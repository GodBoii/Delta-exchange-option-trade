# Shared analysis verification, September 16, 2026

## Behavior implemented

With the Convex runtime enabled, each shared review selects one current built-in strategy and activation time. Independent reviews can run concurrently. Each decision has its own recheck scheduled seven minutes before entry, with a five-minute execution deadline. After that recheck completes successfully, each enabled, connected account receives its own scheduled strategy. The scheduler retries missing allocations after restart; an indexed per-user decision key prevents duplicate allocations.

Each account reads its own current wallet and capital policy at entry. Reservation and lot sizing use that same snapshot. Existing fractions and fixed-amount settings are unchanged. Lot sizing keeps the existing margin/risk calculations, two-percent allowance and minimum-lot rejection.

Shared market context contains no account balances or private active positions. Signed-in users can read shared reports and news results alongside their own history. Strategy executions, credentials and capital remain account-owned. Private templates cannot replace the common catalog selection.

## Changes and cleanup

- Connected the existing shared-decision mutations to fixed/manual review scheduling, AI selection, recheck and account allocation.
- Fixed duplicate allocation detection. It previously searched an index containing strategy IDs using a shared decision ID.
- Added shared recheck lookup to the entry engine and a fresh automation-enabled check before shared entry.
- Reused one strategy-definition validator for ordinary and shared publication. All 15 exported catalog definitions match current model normalization.
- Removed the unused ranked-selection helper and replaced its isolated tests with scheduler, allocation and account-sizing behavior tests.
- Shared allocation uses one pending-work query and up to eight concurrent account mutations. Already allocated accounts cause no repeated allocation writes. Completion triggers allocation immediately; polling provides recovery.
- Removed the second wallet, credentials and capital-policy read from capital reservation.
- Prevented future pre-created fixed reviews from suppressing an immediate manual analysis request.
- Shared history uses indexed owner queries. The overview removes duplicate shared/account proposal rows.
- Preserved and tested the pre-existing local timestamp-serialization fix.

No capital options, production data deletions, orders, production restarts or Git pushes were introduced. Historical Supabase records and migration exports remain necessary until final cutover verification.

## Verification

- Final backend behavior suite: 277 passed in network-disabled Docker.
- Convex/library transaction suite: 28 passed.
- Binance market service: 21 passed.
- Application production build, TypeScript checking and ESLint passed. Python lint passed for the affected modules and tests.
- Tested one decision/recheck across three accounts, distinct budgets/lot counts, repeat allocations, missing snapshots, altered risk, multiple candidates, disabled/disconnected accounts, changed versions, expired entry, incomplete/failed/dropped rechecks and repeated manual requests.
- Convex's 15 imported strategy records still match the saved export exactly. All 15 source records remain in Supabase.
- Supabase source counts match the earlier verification: profiles 4, connections 1, strategies 16, executions 33, execution orders 71, capital settings 4 and automation settings 1. Counts are not a field-by-field audit of these tables.
- Supabase Auth returned HTTP 200; its dashboard reported Healthy. The production backend, analysis and market containers reported healthy.

## Deployment boundary and limits

The additive Convex functions were deployed to `knowing-horse-0` with type checking enabled. Backend changes remain in this checkout. Production still has all four Convex storage switches off, so it has not adopted shared scheduling. The existing stopped-writer migration, source/destination comparisons and coordinated backend/frontend deployment are still required.

The deployed pending-allocation query returned zero pending allocations and rejected an invalid service secret. These checks were read-only.

Before that cutover, drain or cancel old per-user scheduled analyses and proposals through the existing application workflow. Shared mode schedules new reviews under the shared identity; it does not execute old per-user reviews. Keep unresolved execution exposure out of the cutover window.

The pending allocation query explicitly supports up to 1,000 automation-setting rows and 100 future shared decisions. This is a bounded implementation, not an unlimited-scale claim. Entry submission still uses the existing single-writer execution loop and exchange rate limits. Allocations are account-specific; exchange fills cannot be guaranteed at precisely the same instant.

Laptop Convex reads measured 353–1,909 ms in this check, with most later reads near 350–670 ms. This is network latency evidence, not an end-to-end production benchmark. Real exchange execution and authenticated browser workflows after cutover remain deployment checks; no funded trades were used as tests.
