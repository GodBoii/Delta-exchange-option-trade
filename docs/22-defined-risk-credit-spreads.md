# Defined-risk BTC credit spreads

The 17-24 September decision audit found 42 completed reviews with reports. Most
no-trade decisions cited catalyst uncertainty, expensive long premium, or a
one-sided range in which naked short options were unattractive. No-trade remains
a valid outcome. A spread is only a candidate when its own market and execution
criteria hold; the agent must not force a trade into an ambiguous event window.

Two new shared templates cover the mild directional range case:

| Strategy | Entry order | Market thesis | Expiry payoff risk |
| --- | --- | --- | --- |
| Bull put credit spread | Buy farther OTM put, then sell nearer OTM put | Support holds; BTC stays flat or rises | Strike width minus collected credit |
| Bear call credit spread | Buy farther OTM call, then sell nearer OTM call | Resistance holds; BTC stays flat or falls | Strike width minus collected credit |

Both legs have the same expiry and size. The long wing is submitted first because
Delta does not atomically place orders for different option products. The
existing exit code buys shorts back before selling hedges. Existing 80% gross
credit target, strategy monitor and Delta-hosted short-leg emergency bracket
remain in force. They may close a trade before expiry. The agent may select a
shorter supported holding period. Neither strategy has proven positive expected
returns from this one-week sample.

At activation the engine requires executable bid and ask quotes, reads the
actual contract multiplier, strike width and Delta fee rate, and rejects a
nonpositive credit or one that does not cover estimated entry and exit fees
including GST. This is an entry gate, not a guarantee of final P&L. The engine
uses Delta's authenticated selected order leverage for short-leg margin
estimation; it does not change that leverage. Delta still performs the final
margin check. The account capital budget and contract leverage remain distinct.

The full analysis audit also found 12 no-trade outcomes whose model-authored
reports claimed a selection without a committed scheduling action. New reports
lead with the recorded action, which is authoritative. The original model text
remains available for review. This prevents an unexecuted suggestion from being
mistaken for an exchange order.

The two templates are inserted into the Convex-owned shared library using the
idempotent `library:serverCreateDefault` mutation. Seeding skips existing
templates and does not rewrite existing runs or strategy versions. The old
PostgreSQL library is retired; no SQL migration is required.

Do not restart the trading writer while any strategy remains scheduled, active,
exiting or in attention, or while Delta has an open position or order. Build the
new images before the final flat check, then restart the writer and analyzer and
verify both health endpoints, scheduler status, and account exposure. Seed the
new templates only after the new backend is healthy. Confirm a subsequent
`show_available_strategy` result contains both names and the existing 13 BTC
templates. If the account cannot become flat, leave the writer running and
defer rollout rather than interrupting risk monitoring.
