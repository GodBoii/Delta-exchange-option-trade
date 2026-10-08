# Scheduled entry outcomes

An entry rejected before execution ends as `skipped`, with its original reason in
`last_error` and structured details in `entry_outcome`. The strategy list and detail
APIs expose the latter as `entryOutcome`. Skipped entries stay in history and do
not increase attention counts or contribute to P&L.

The reason categories distinguish occupied strategy slots, reserved account
capital, low balance, an insufficient strategy budget, authorization, market data,
network failure, system failure, activation recheck rejection, and an expired entry
window. Slot exhaustion and reserved capital have separate error codes,
`capital_slots_full` and `capital_reserved`.

Transient failures keep the entry scheduled during the configured lateness window.
The scheduler records the latest failure without sending repeated rejection
notifications. If the window expires, the skipped outcome retains that failure in
`lastFailure` and includes its message in the recorded reason. Skipped entries are
never automatically replayed. Genuine partial executions and uncertain exposure
continue to use `attention` and the existing recovery path.

Risk failures are cleared when their strategy is completed, cancelled, skipped,
or deleted. Failures on active or unresolved attention strategies remain. The
Compose health check still requires a running scheduler, a completed polling
cycle, and no scheduler error.

## Existing records

Migration `010_entry_outcomes.sql` reclassifies only explicit `Entry not placed:`
rejections with no entry or exit timestamp, execution, order intent, product claim,
or capital reservation. It preserves the original message and records the prior
status. Historical reasons that cannot be inferred exactly remain unclassified.
After migration, run `python -m scripts.backfill_owner_ledger` in the backend
container to refresh the owner reports.

## Ubuntu deployment, October 8, 2026

Only the `delta-exchange` service was rebuilt and recreated. A fresh PostgreSQL
dump was saved before deployment. All 21 historical attention records qualified
for the guarded migration; each retained the reason that account capital was
already reserved. The owner ledger reconciled with zero missing or mismatched
records. Repeated Docker checks passed, with zero current failures and zero
scheduler risk errors. The public health endpoint returned HTTP 200.

Validation included 44 focused Python tests against disposable PostgreSQL,
client type checking, linting of the changed files, and 32 client tests. The full
isolated database suite returned 533 passes and six failures. All six reproduced
with the pre-change application code: missing offline test API-key configuration,
a test URL assuming a particular database name, and four outdated strategy
fixtures. Browser verification was unavailable because the browser tool timed out.
