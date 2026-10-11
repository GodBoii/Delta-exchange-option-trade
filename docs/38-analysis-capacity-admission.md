# Analysis admission across BTC, ETH and accounts

New-entry analysis checks both funded capacity and pending commitments before the
analyzer is called. This extends the read-only capacity check in document 30.
Activation rechecks remain required. Stops, targets, exits, order recovery, sizing,
atomic entry reservations and accounting retain their existing execution paths.

## Capacity decisions

Each connected account uses its own capital policy and asset participation switches.
Percentage policies retain their one, two, three or four slots. Fixed budgets retain
the wallet-derived limit and 100-slot ceiling. A shared review proceeds when any
account enabled for that asset has confirmed room. Eight bounded capacity workers
check accounts, and a healthy account can proceed without waiting for a slow wallet.
There is no account-count threshold that disables the check.

The writer reads a consistent PostgreSQL snapshot of the wallet's aliases, reserved
and active slots, scheduled account entries, pending shared decisions and unfinished
analyses. Existing reservations always count until the execution engine releases
them. Expected exit times never release funded capital early. Owner reporting and
display wallet caches do not determine admission. Private capacity data is not sent
to the model.

Pending work contributes an estimated allocation under each participating account's
current policy. Shared proposals and their account copies count once per account.
Funded copies replace their pending commitments. Aliases retain existing per-login
slot limits but share the wallet's reserved and expected budget. Exact contract
affordability remains a live entry check; positive admission is not a fill guarantee.

## BTC priority and parallel work

Due BTC reviews receive first consideration within the main and recheck pools.
An unfinished main review is treated as one possible trade until its committed
outcome or completion is known. A due, still-valid queued BTC review also receives
priority over ETH where the wallet cannot fund both. No-trade and follow-up outcomes
release the main review's commitment. Future follow-ups do not hold capacity before
they become due.

If every otherwise eligible account would be exhausted by pending work, ETH remains
scheduled with `analysis_capacity_pending` instead of paying for a model call. The
same rule applies to redundant BTC reviews. The scheduler reevaluates them every
poll until the existing ten-minute review lateness limit expires. It keeps the
original run ID and scheduled time. It does not create replacement jobs or replay
cancelled reviews.

An account with spare slots and budget can support concurrent BTC and ETH analysis.
For example, a fixed-budget account with capacity for 100 trades can admit both
while smaller accounts wait. A full owner account does not block that account.
Different asset switches can also permit both reviews on separate eligible wallets.

## Rechecks and recovery

A recheck excludes its own proposal and account copies. Its admission uses the
assigned entry-to-exit interval, so nonoverlapping pending trades do not compete.
Overlapping proposals receive deterministic BTC-first, then activation-time and ID
priority. Running main reviews cannot prevent an already-selected proposal's
recheck. An existing manual scheduled trade remains a commitment.

Failed, cancelled and dropped approvals cease to contribute pending commitments.
Allocated decisions count through their scheduled account copies. An account that
became eligible after completed allocation is not assumed to receive that decision.
Pause and disconnect switches remove hypothetical participation; actual wallet
reservations continue to count.

The gate runs again after claim. Persisted start time and run ID order concurrent
main claims so a later claim cannot cancel the earlier one's admission. If capacity
became pending or unknown, the claimed job returns to scheduled before any analyzer
request, provided it has no committed outcome. Unknown wallets remain unknown rather
than being reported as full. Accepted analyzer requests retain their existing
no-automatic-retry behavior.

Confirmed funded-slot or wallet exhaustion keeps the existing cancellation and skip
report. Pending and unknown deferrals retain distinct public reasons on the run and
in structured logs. A subsequent poll can cancel a pending run once BTC consumes
the last funded slot. Restart recovery retains the existing handling of interrupted
analyses and reconstructs pending commitments from persisted trading records.

## Verification

Tests cover percentage and fixed budgets, 100-slot capacity, aliases with different
policies, asset participation, concurrent claims, selection/no-trade/follow-up
handoffs, shared/account deduplication, private and shared recheck self-exclusion,
BTC priority, disjoint holding windows, unknown wallets and post-claim deferral.
An isolated PostgreSQL scenario verifies two 100% accounts start only BTC main
analysis and skip ETH after BTC occupies both accounts.

Verification on October 12, 2026:

- Local backend suite: 597 passed, 94 skipped. PostgreSQL tests require the Ubuntu
  test environment.
- Ubuntu backend suite with disposable PostgreSQL: 682 passed, eight skipped,
  one existing library API fixture failure. The fixture submits an incomplete
  strategy and expects HTTP 200; current validation returns 422. The same failure
  reproduced with the pre-change `4db6d7a` backend modules.
- Focused admission, capacity, runtime, scheduling, dispatch and entry-outcome
  checks passed against PostgreSQL. All changed Python files passed Ruff.
- Admission integration tests create a separate database on the explicitly
  configured disposable test server, requiring CREATEDB permission. This prevents
  leftover global jobs from contaminating subsequent test runs.

No migration or frontend change is required. Deploy the writer with the new
`app.analysis_admission` module and the matching engine, runtime and scheduler.
