# Offline option execution replay

`backend/scripts/option_replay.py` contains read-only replay mathematics. It has
no exchange client, database writer, credentials, or order-submission authority.
Keep account exports and generated artifacts under the Git-ignored `data/`
directory. Do not commit financial account snapshots with the replay code.

Replay exact option symbols, contract multipliers, actual filled quantities,
opening prices, saved schedules, combined risk basis, and the exchange brackets
actually attached to entry orders. Independently reconstruct actual outcomes
from ordinary and settlement fills before comparing paper alternatives.

Use separate historical series for the underlying perpetual chart, the option
settlement index, and each exact option's mark. Expiry intrinsic payoff is not a
valid valuation for an earlier exit while time value remains. Entry premium is
cash flow, not an immediately realized profit.

`latest_candle` exposes only completed minute observations. The entry minute's
closing price is available after entry, but its high/low can include a pre-entry
move; the range-trigger variant therefore does not infer an emergency crossing
from that partial entry bar. Missing evidence stays unavailable.

`replay_policy` calls production `strategy_level_metrics` for combined stop and
target arithmetic. Its default variant evaluates completed closes. Setting
`intrabar_emergency=True` also detects emergency-stop crossings in full-bar
ranges, then values paper exits at the completed bar's marks. This approximates
intraminute protection; it does not promise a stop-threshold or executable fill.
Different legs' extrema are not assumed to occur simultaneously. Recorded
intrabar combined crossings are diagnostic bounds, not automatic fills.

Keep these comparisons separate:

- Actual fills, closing events, and commissions reproduce the real result.
- Fill-time mark metadata at the same actual closing events isolates execution
  difference without changing the exit decision. Compare only covered trades.
- Historical completed-minute mark repricing also includes timing differences
  between the sampled mark and the actual fill.
- Alternative stop/target/deadline replays change the exit policy and require
  estimated alternative commissions. Mark fills assume liquidity.
- Frozen entries and sizes isolate exit behavior, but do not constitute a
  self-financing strategy backtest after hypothetical equity or capital changes.

Option bid/ask replay requires causal, sufficiently fresh quotes and depth for
the actual quantity. A later snapshot or a stale observation must not be used
as an exact historical execution price. State the observation age and coverage.

Report entry and exit execution costs separately. They are already incorporated
in actual fill P&L; subtracting them again double-counts the loss. Likewise,
actual commissions already including GST must not have GST deducted twice.

Tests cover no future-bar access, partial entry bars, flat-underlying option
losses, emergency-stop round trips, intrabar ambiguity, missing evidence,
contract-unit fees, and expiry breakevens.
