# Emergency stop trial

The user requested a seven-day observation period with emergency stops restored to
300%. They clarified that the combined stop must remain at 100%.

- Applied to the production library on October 1, 2026 at 16:12 IST,
  `2026-10-01T10:42:03.370918+00:00`.
- Review the following seven days through October 8, 2026 at 16:12 IST.
- Updated five shared templates: ETH Short ATM straddle, Short ATM straddle,
  Short OTM call, Short OTM put, and Short strangle.
- Changed only `emergencyStopLossPercent` from 170 to 300, incrementing each
  template version. Verified full definitions in one transaction, including
  unchanged combined stops and an idempotent second pass.
- Backend canonical templates and the builder's new-strategy default now use 300%.
- Existing run snapshots and exchange-hosted orders were not changed. At rollout,
  the database contained two active runs and seventeen attention runs.
- A 300% emergency loss threshold places the short-leg backup stop at four times
  its entry premium. The combined 100% loss threshold remains twice entry credit.
- No automatic reversion is scheduled. Review results before changing policy again.

Verification passed: 68 backend tests covering templates, risk calculations, and
execution boundaries; TypeScript typecheck; Ruff for changed Python files; and
Git diff checks.

The library update is available as `shared_library.py emergency-stop`, with a
dry run by default and `--apply` to commit. It updates shared short-leg templates
for future entries and does not modify run snapshots or exchange orders.

A heartbeat named "Review 300% emergency stop trial" is scheduled for seven days
after setup. It will inspect production results without changing trading settings
and pause after reporting. Compare fees-adjusted P&L, completed trade counts,
win/loss counts, largest loss, stop-exit reasons, and unresolved trades with the
preceding seven days. Separate BTC and ETH and avoid counting shared outcomes
multiple times. Include only new entries whose definition uses the 300% emergency
stop, and state any missing accounting data.
