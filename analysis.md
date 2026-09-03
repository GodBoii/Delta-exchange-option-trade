I'll dig into the codebase to understand the system before analyzing.I'll start by orienting on the backend app structure and reading engine.py.I read the automation agent, the market packet builders, the execution engine, the strategy definitions, and the schema. Here's what I found, organized against your questions.

Two things I could not verify from code alone, and both are cheap to check: the actual `outcome`/`error`/`tool_calls` values for your last 3 days of runs, and how many BTC option rows Delta actually returns. Both matter to the diagnosis below.

---

## 1. Why the agent has not traded for 2–3 days

There are five independent causes. At least one is a mechanical bug, not a judgment call.

### 1a. Likely mechanical: the option chain is truncated to 100 rows

`MarketIntelligenceTools.collect_delta_option_context` (market.py) calls `/v2/tickers` with `page_size: 100` and never follows `meta.after`. Delta lists BTC calls and puts across same-day, next-day, weekly, monthly and quarterly expiries. That is several hundred rows minimum. The agent sees an arbitrary 100-row slice in Delta's default ordering.

This is not just a display problem. `select_strategy_and_time` reads the *same stored truncated list* back out of `automation_market_snapshots.market_json.deltaOptionContext.options` and passes it to `resolve_option_expiry`. Six of your eight strategies use `expiry_policy: same_day`. If the same-day expiry is not in the 100 rows:

```
ValueError: No listed Delta expiry satisfies the same_day policy
```

The tool throws, Agno hands the error back to the model, and the model writes a no-trade report that reads like a considered judgment. You would never see the cause in the UI.

Check this first: count the BTC option rows Delta returns. If it is 100 or more, this is very likely your blocker.

Two related defects in the same function: the `/v2/products/{symbol}` settlement lookups run sequentially in a loop with a 20s timeout each, and on any failure fall back to `_expiry_from_symbol`, which hardcodes 12:00 UTC. That happens to be correct for Delta BTC dailies today, which means the bug is invisible until it isn't.

### 1b. The activation-time race

`select_strategy_and_time` calls `_future_datetime(activation_time)` which raises if the timestamp is not in the future *at the moment the tool executes*. The model picks that timestamp early in its reasoning. The run has `reasoning_effort="xhigh"`, six high-detail images, and a news sub-agent that your own logs recorded at ~199 seconds. `MAX_AUTOMATION_RUN_RUNTIME` is 20 minutes.

If the model decides "activate 05:45 IST" during a run that started at 05:30 and reaches the tool call at 05:47, the call hard-fails. This is not an edge case at these latencies.

### 1c. No-trade is the only free outcome

`execute_automation_run` defaults to `payload.get("outcome") or "no_trade_for_current_window"`. No trade requires zero tool calls and zero validation. The other two outcomes each have a dozen `raise ValueError` paths: activation in the past, expiry resolution failure, follow-up already used between fixed reviews, terminal outcome already claimed, slots full, version changed.

Gradient descent on effort points at abstaining.

### 1d. The instructions are a veto with no counterweight

From team.py:

> "If evidence is stale, contradictory, incomplete, or outside a saved strategy's gates, do not select a strategy."

Market evidence is always somewhat contradictory and always incomplete. There is no matching positive trigger anywhere in the instruction list. Nothing says "a ranging regime with rich implied volatility and no high-impact event in the holding window is sufficient basis to sell premium." So the model has an unconditional brake and no accelerator.

The strategy descriptions in `default_strategies.py` compound this. Every one of the eight ends in a disqualifier. The short straddle: *"Avoid trends, breakouts, news events, or rising volatility. Both short legs carry uncapped tail risk."* In crypto there is always some upcoming event and always some risk headline. A careful reader of eight paragraphs each saying "avoid unless conditions are perfect" concludes nothing is ever perfect.

### 1e. The agent has no way to compute the edge, so it abstains

This is the deepest cause and the highest-value fix.

To justify short premium you need one comparison: implied move to expiry versus expected move. What the agent actually receives:

- `atr: {value: 111, percent: 0.139%, period: 14}` — a **1-minute** ATR over 14 minutes
- `historicalVolatility: {annualizedPercent: 52.9, sampleSize: 120}` — annualized from **120 one-minute returns**
- `sidewaysProbability: 83` — computed from `closes[-60:]`, so the **last 60 minutes**
- `marketStructure` — EMA20/EMA50 run over the whole 500-bar 1m array from `values[0]`
- raw per-contract `impliedVolatility`, `delta`, `gamma`, `theta`, `vega`

Nothing computes the ATM straddle price, the implied move to settlement, an IV/RV ratio, IV rank, breakeven distances, or theta per lot. The model is handed numerically incomparable quantities and asked to eyeball a relative-value decision under a token budget.

This is also the direct answer to your sideways-probability observation. An 83% score derived from **60 minutes of 1-minute closes** is being used to justify a **7-hour** short straddle. The model is right to discount it. Your empirical intuition that >75% usually means a sideways day may well be correct, but the number as computed does not carry that horizon, so the model cannot rely on it and neither can you without validating it.

---

## 2. Iron condor and iron butterfly: you are mostly right

Your argument is that the wings are redundant given monitoring plus a 100% stop. That holds better than you might expect, and for a reason you did not mention.

**Where you are right:**

The monitored stop fires at `entry_credit × (1 + stopLossPercent/100)`, so loss is capped near 1× credit. The wings cap loss at `wing_width − net_credit`, which on BTC strike spacing is typically several multiples of the credit. The structural limit sits far outside where your software stop actually fires. The wings almost never bind.

Worse, `apply_automatic_lots` sizes `defined_max_loss` strategies on `width − credit` rather than `credit × stop%`. So iron condors get sized *smaller* for the same capital while being protected by the same 100% stop that protects the naked version. You pay premium for insurance you never reach, get less size, and collect less theta.

And `execute_entry` submits legs sequentially and `break`s on the first failure. A 4-leg structure has roughly twice the exposure to landing in `attention` with live one-sided risk compared to a 2-leg one.

**Where the wings genuinely earn their keep:**

1. The stop is polling-based over REST and requires *all* legs fully filled before arming (`risk_state.status == 'awaiting_fills'`). Between submission and full fill, and during any backend, network or Delta outage, there is no stop at all. Wings are exchange-resident. `PROJECT_HISTORY.md` already documents a 9.5-hour late exit caused by local downtime.
2. When the stop fires on an ATM short straddle during a fast move, you exit with market reduce-only orders into a widened option book. Realized loss can exceed 100% of credit meaningfully.
3. Delta charges far lower initial margin on a spread. Note that `risk_per_lot = max(risk_per_lot, estimated_order_margin)` in `apply_automatic_lots`, so for naked shorts the margin floor may already dominate your credit-based sizing. Worth measuring on the real account before assuming naked shorts give you more size.

**The resolution:** your instinct becomes fully correct if you fix one thing. `default_strategies.py` sets `emergencyStopLossPercent: 300` for credit strategies, which places the exchange-hosted stop at mark × 4.0 per short leg. That is decoration, not protection. Tighten it to roughly 150–200% so the exchange holds a real backstop on the naked shorts, and the iron structures become genuinely redundant. Then either delete them or demote them to a fallback used only when the backend has been unhealthy or a high-impact event lands inside the holding window, which requires giving the agent health and event data it does not currently receive.

---

## 3. Delta perpetual data: your concern is correct, and the fix is structural

You are right that Delta India BTCUSD perp is a different instrument. Your log shows $46M OI and $1.76B 24h turnover against Binance's $2.4B on spot alone. Its OI deltas and funding are dominated by local retail positioning and are close to noise for BTC direction.

The instruction *"Do not use Delta perpetual volume to predict BTC direction"* exists. Two things undermine it:

**The charts contradict the text.** `_chart_artifacts` renders a Delta BTCUSD open-interest chart and attaches it as `detail="high"` alongside the price charts, with `alt_text` reading "Delta BTCUSD open-interest history chart". A vision model handed six images weights all six. A prose instruction is weak against an attached image that is presented as legitimate evidence.

**The payload does not separate the two.** `compact_btc_market_packet` returns `deltaExecutionMarket` with `openInterestBtc`, `openInterestChange6hUsd`, `fundingRatePercent` and `markBasisPercent` as a sibling of the Binance ticker in one flat object. The separation exists only in the instruction list.

Fix it in the data shape, not the prompt: two explicitly named top-level blocks, `directionalEvidence` (Binance only) and `executionAndPricing` (Delta only). Drop the Delta OI chart from the image set. Structure beats instructions with LLMs.

One correction to your framing though: funding and basis *are* legitimately useful, as crowding and positioning indicators rather than direction predictors. And Delta's **option** IV, OI and skew is the most valuable dataset you have and currently the least processed. Do not throw that out with the perp volume.

---

## 4. Past trades are not provided at all

`build_account_context` returns `openOrders`, `positions`, `activeStrategies` (filtered to scheduled/executing/active/attention), `nextFixedAgentRun`, `upcomingAgentRuns`, `maximumConcurrentStrategies`.

There is no completed run history, no `realized_pnl`, no `result_json`, no prior proposal with its outcome, and no prior report. And `stored_session_id` is `automation:{user_id}:{session_id}` where `session_id` is `scheduled-{run_id}`, so **every run is a brand-new Agno session**. There is no chat history carry-over either.

The agent is fully amnesiac. This feeds directly into the abstention problem: with no evidence that any strategy has ever worked, the prior on all eight is "unknown, therefore risky."

The data exists and joins cleanly: `strategy_proposals.strategy_id → strategies.realized_pnl / result_json / risk_state.exitReason / status`, plus `execution_orders` for per-order slippage, plus `automation_agent_runs.report_markdown / outcome` for the decision trail including no-trades.

Add a `get_recent_decision_history` tool returning the last ~20 terminal decisions: timestamp, trigger, outcome, strategy, the regime numbers from that run's snapshot, confidence, and for executed runs the realized P&L, exit reason, holding duration and slippage cost. **Include the no-trade runs.** "You have recorded no trade eleven times across three days" is itself decision-relevant.

Caveats to build in: `realized_pnl` is only written when `closedLots > 0`, runs ending in `attention` leave it null with the money view only in `result_json`, and `delete_strategy` hard-deletes history. Report unknowns as unknown rather than zero.

---

## 5. Your pre-execution run idea: good, with one correction

The idea is right and the codebase supports it cleanly. `AutomationScheduler` already polls `automation_agent_runs` for due rows, honours lateness, and dispatches with a session id. `select_strategy_and_time` can insert a second row in the same transaction as the strategy and proposal. No new scheduler needed.

**The correction is the timing.** T−5min does not fit. The full team run takes minutes; the news agent alone has been recorded at ~199s; the httpx timeout is 900s and `MAX_AUTOMATION_RUN_RUNTIME` is 20 minutes. If the check overruns you either execute unchecked or blow past `max_entry_lateness_seconds = 180s` and get rejected to `attention`.

Split it into two layers.

**Layer 1: deterministic gate at T−60s, no LLM, inside the engine.** This is what protects money and it must never be slow or skipped:

- `proposal_expiry` has not passed. **This is written and never read anywhere.** Real bug.
- `saved_strategies.version` still matches `saved_strategy_version`. Checked only at proposal time; the user can edit the saved strategy in between and the frozen snapshot executes regardless. Real bug.
- Feed health: Binance `realtime.connected` and `bookSynced` true, `eventAgeMs` under threshold, Delta `tradingStatus == "operational"`.
- Leg liquidity: spread as a percentage of mark under a cap, `bidSize`/`askSize` sufficient for intended lots, mark greater than zero.
- Numeric regime guards evaluated against the proposal.

On failure, `reject_scheduled_entry` already does exactly the right thing. Add the new codes to `TERMINAL_SCHEDULED_ENTRY_CODES`.

The key architectural change: **make `invalidation_signals` machine-checkable.** Right now the agent writes free-text invalidation conditions into `strategy_proposals.invalidation_signals` and nothing ever reads them. That is precisely the gap you are sensing. Change the tool signature so invalidation is a typed structure the engine can evaluate (`maxSpotMovePercentSinceSnapshot`, `minSidewaysProbability`, `maxAtmIvChangePercent`) and keep the prose in a separate human-readable field.

Note that your own `docs/14-automated-strategy-system.md` section 11 already claims "Latest conditions and saved version revalidated." It does not happen. The only checks at entry are mechanical feasibility: wallet balance, free slot, chain availability, live prices, margin.

**Layer 2: LLM confirmation at T−10min**, as `trigger: 'pre_execution'`. Give it:

- the prior run's `report_markdown`, `reasoning_summary`, `supporting_signals`, `invalidation_signals`, `ai_confidence`
- a **computed diff** against the prior snapshot: spot change, ATM IV change, sideways-probability change, straddle price change. Do not hand it two JSON blobs and expect arithmetic.
- fresh charts, but only 1m and 15m price
- resolved legs with live quotes and spreads
- exactly two actions: `confirm_scheduled_strategy` or `cancel_scheduled_strategy`. Do not let it re-select or reschedule, that reopens the full decision and blows the budget.
- no news sub-agent, lower reasoning effort, hard ~90s wall clock

**Write down the failure default and implement it.** If the confirmation run times out or errors, the trade should **proceed** on the strength of Layer 1. A silent LLM failure must not be able to veto every trade, which is the exact failure mode you are already in.

One accounting detail: the pre-execution run must be excluded from `maximum_agent_runs_per_day` and from the "one follow-up between fixed reviews" rule in `scheduled_next_agent_run`, or it will consume the follow-up budget and start throwing.

---

## 6. Strategy coverage: the real gap

Your eight strategies cover the volatility axis reasonably and the direction axis badly.

| | Vol expanding | Vol stable | Vol contracting |
|---|---|---|---|
| **Strong up** | long call | long call | — |
| **Slow drift up** | — | **nothing** | **nothing** |
| **Flat** | long straddle/strangle | short straddle, IB | short straddle, IB |
| **Slow drift down** | — | **nothing** | **nothing** |
| **Strong down** | long put | long put | — |
| **Range, wide** | — | short strangle, IC | short strangle, IC |

The empty row is the most common BTC regime: slow grind in one direction. A long call bleeds theta in a grind. A short straddle gets run over. You have nothing that profits from "probably up, or at least not sharply down."

**Add four verticals, in priority order:**

1. **Bull put credit spread** (sell OTM put, buy further OTM put). Neutral-to-bullish, defined risk, positive theta, wins if BTC goes up, sideways, or mildly down. This is the single biggest gap.
2. **Bear call credit spread**. The mirror.
3. **Bull call debit spread**. Strong bullish when IV is rich; beats a naked long call on cost and vega.
4. **Bear put debit spread**. The mirror.

Two implementation notes:

- The **debit** spreads are currently blocked by `models.py`: `if self.riskBasis == "net_debit" and any(leg.position == "sell" for leg in self.legs): raise`. That validator needs relaxing. Not a config change.
- The **credit** spreads work today with `riskBasis: defined_max_loss` and `riskMode: strategy_level` (not `combined_premium`, which requires two short legs). `monitor_combined_strategy` accepts `strategy_level`, and the `defined_max_loss` width calculation in `apply_automatic_lots` handles same-type short/long pairs correctly.

**A correctness problem in your existing OTM strategies.** `strikeSteps` means "N listed strikes away," and `resolve_leg` silently clamps to the outermost available strike when the steps run off the end of the chain. `strikeSteps: 2` on a same-day chain (strikes ~200 apart) is a completely different trade from `strikeSteps: 2` on a monthly chain (strikes 1000+ apart). Your short strangle, iron condor and iron butterfly are therefore different trades on different days from the same saved definition, and the silent clamp means a leg can execute at a strike nothing like what was intended, with no error recorded. OTM distance should be expressed in ATR or implied-move units and converted to steps at resolution time.

**A design ceiling worth knowing:** `sameExpiryRequired` defaults true and `materialize_live_definition` overwrites every leg's expiry with one resolved date. Calendars and diagonals are structurally impossible. For BTC, same-day versus weekly term structure is a real edge, so this is a ceiling you will eventually want to lift.

---

## 7. Other improvements

**Compute the pricing edge server-side.** Add to the packet: ATM straddle price and implied move per expiry; implied move derived from ATM IV and actual time to settlement; realized vol on matched horizons (1h, 4h, 24h, 7d, not 120 minutes of 1m returns); IV/RV ratio; IV rank against a trailing window (backfillable from stored snapshots); 25-delta risk reversal; put-call OI ratio. Then, per saved strategy at the live chain: entry credit or debit, both breakevens, breakeven distance in ATR and implied-move units, max profit, structural max loss, theta per lot. Hand the model "short strangle: credit 340, breakevens 1.9% out, implied move to expiry 1.2%, RV-implied 0.8%, theta 41/hr" and it will trade. Hand it raw quotes and it abstains.

**Fix the horizon mismatch.** `calculate_analysis` is 1-minute only and labels itself so, yet it is the sole quantitative regime input. Compute the same family on 15m and 1d. Anchor VWAP to the IST session or the expiry cycle rather than a rolling 240 bars. Give `sidewaysProbability` multiple stated windows (60m, 4h, since session open) and expose its components (efficiency ratio, range %, VWAP deviation) rather than only the blended score, which hides why it is high.

**Replace the veto-only prompt with a rubric.** Keep the hard vetoes but make them few and specific (high-impact event in window, stale feed, spread above cap, no liquidity). Add explicit positive triggers keyed on the computed features. Require a filled scorecard per candidate before selection. State that no-trade has a cost when premium is rich, so it stops being free.

**Surface tool failures.** Right now a `ValueError` from `select_strategy_and_time` and a genuine judgment call both render as `no_trade_for_current_window`. Write the tool error into `automation_agent_runs.error` even on "completed" runs, add a distinct outcome such as `blocked_by_tool_error`, and show it in the Automation UI. This one change would have answered your question in five seconds instead of requiring this analysis.

**Risk monitor latency.** `process_active_risks` loops serially over up to 25 strategies on the same coroutine that runs entries, exits and reconciliation. Each iteration does an executions select, a paginated fills reconciliation, N ticker calls, and DB updates. The nominal cadence is 2s; real stop latency is that whole serial chain. On a naked ATM straddle that is the difference between a 100% stop and a 140% stop. In order of value:

1. Latch `fillsConfirmedAt` in `risk_state` and stop re-running `reconcile_entry_fills` every cycle once legs are confirmed. This removes the most expensive call from the hot path.
2. Move the monitor to its own asyncio task so a slow entry cannot stall it.
3. Stream option mark prices over the Delta websocket instead of per-leg REST `ticker`. Your `binance_backend/app/feed.py` already demonstrates you can do proper websocket plumbing with order-book sequencing. This takes stop latency from seconds to sub-second.

Do this before you scale size.

**Slippage and stale-price guards at submission.** `execute_entry` places market orders with no check between the mark it captured and actual submission. Reject if spread % exceeds a cap, if the mark is older than N seconds, or if the resolved strike drifted from what the proposal assumed. `PROJECT_HISTORY.md` section 4.6 already lists this as outstanding.

**Link the news analysis.** `news_analysis_id` is accepted by `AutomationStrategyTools.__init__` and never passed by `team.py`, so it is always null on every proposal. You cannot audit what news drove a trade.

**Reuse the Agno session.** Use a stable per-user session id with bounded history instead of a fresh one per run, so the model sees its own recent decisions natively. Cap the history to control tokens.

**Prompt caching.** `context.md` lists the OpenRouter caching docs but I see no `cache_control` usage. Team instructions, strategy definitions and tool schemas are large and identical across runs. Caching cuts cost and, more usefully, time-to-first-token, which is exactly what your T−10 confirmation budget needs.

**Chart quality.** `charts.py` uses `ImageFont.load_default()`, draws no time axis anywhere, no axis labels on the line and bar charts, and no VWAP or EMA overlays on price. You are paying vision cost for six `detail="high"` images and getting gross shape recognition. Cut to three charts and make those three properly readable: time axis, price axis, VWAP and EMA lines, and markers for the proposed entry and expiry times.

**Minor inconsistency:** the automation HTTP call times out at 900s but the run is not marked failed until `MAX_AUTOMATION_RUN_RUNTIME` of 20 minutes. Align them.

---

## Ordered plan

1. Query `automation_agent_runs` for the last 3 days: `outcome`, `error`, `tool_calls`. Determines mechanical versus judgment. Do this before anything else.
2. Count BTC option rows from `/v2/tickers`. If ≥100, fix pagination.
3. Surface tool errors as a distinct outcome.
4. Compute the pricing-edge block and rewrite the prompt as a rubric. This is what makes it trade.
5. Add the decision-history tool.
6. Deterministic T−60s gate, enforce `proposal_expiry`, re-check saved version, make `invalidation_signals` typed.
7. Add the four vertical spreads; relax the `net_debit` validator for the debit pair.
8. Tighten `emergencyStopLossPercent` on naked shorts to ~150–200%, then decide whether the iron structures stay.
9. T−10min LLM confirmation run.
10. Risk-monitor latency work before scaling size.

Steps 1 and 2 are the ones I would not skip. Everything else in this list is a real improvement, but if there is a truncated option chain silently killing every `select_strategy_and_time` call, none of the prompt or data work will change the outcome.