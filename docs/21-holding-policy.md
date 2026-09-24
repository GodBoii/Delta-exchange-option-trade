# Agent holding decisions

The saved seven-hour interval is a fallback, not a scheduler limit. The scheduling
tool takes a short `strategy_ref` from `show_available_strategy`, chooses
`holding_policy` and optionally `expiry_policy`, and derives proposal expiry
server-side.

| Policy | Required exit | Schedule |
| --- | --- | --- |
| saved | None | Preserve the template duration or expiry hold |
| intraday | Aware ISO timestamp | Exit on the entry date in Asia/Kolkata |
| overnight | Aware ISO timestamp | Exit on the following date in Asia/Kolkata |
| positional | Aware ISO timestamp | Exit at the chosen time before the expiry buffer |
| hold_to_expiry | None | Exit at contract expiry minus the saved buffer |

The supported expiry policies remain same_day, next_day, 7_day and 30_day. The
resolver requires a listed contract satisfying the selected policy. Explicit
timed exits past its safety buffer are rejected rather than silently shortened.
Stops and profit targets can close the position earlier. The tool does not extend
or modify an existing trade; its choices apply when scheduling a new run.

Timed overnight and positional holds use the existing `holdingMode=intraday`
wire value for clock-based exits and `entry.strategyType=btst` or `positional`
for the horizon. This preserves the existing UI and history schema. The stored
exit timestamp controls the scheduler. These labels do not imply guaranteed
holding until that time.

Risk parameters, order types, strikes and sizing rules still come from the saved
strategy. The agent explains its horizon in the existing reasoning summary. It
must justify the entire hold using available market evidence, including event
risk, liquidity, volatility and distance from short strikes.

New built-in and builder defaults use a 50% take-profit target. Credit strategies
target a buyback cost at 50% of entry credit; debit strategies target a sale value
at 150% of entry debit. These are mark-based gross thresholds, before fees and
execution slippage. Built-in entry legs use market orders. Engine exits already
use reduce-only market orders. Custom manual limit orders remain supported.

Use `python -m scripts.set_take_profit --apply` from `backend` to update the
Convex-owned shared library with version checks. The command refuses to write
while a strategy run is open and can be rerun safely.
Existing scheduled, active and historical run snapshots are not rewritten.
The agent tool and prompt changes require a backend deployment; Convex runtime
users also require the updated materialized-definition validator.
