# Market evidence and agent tools

BTC and ETH use one tool-based workflow. There is no input-mode flag.
The system collects a run-bound market snapshot and three main-analysis price charts.
The main agent calls its asset market tool and `show_available_strategy` to read
market evidence and saved strategies before deciding. News research, saved financial
controls, account sizing and order execution remain separate responsibilities.

## Input and tools

Market collection includes the market service's `/agent-summary` response. Tool results
retain spot indicators, timeframe summaries, dated session history, historical baselines,
trade-flow windows, current and previous liquidity summaries, and separate futures venues.
Units, timestamps, observation coverage and missing values remain available. Raw candles,
trades, books and complete option chains stay in private snapshots rather than model text.
Option overview data is not included in the main market tool result.

Session history includes the last one and two hours plus every fixed-session interval
back through the previous opening of the currently active session. During Asia, for
example, it includes the current partial Asia session, intervening sessions and the
previous Asia session. Manual, follow-up, delayed and activation recheck runs use that
same active-session boundary. Sideways values are averages of sampled 60-minute scores,
not calibrated probabilities. Missing history remains unavailable, with coverage shown.

Main runs receive 1-minute, 15-minute and daily price charts. Volume, realized
volatility and order-book evidence arrive in the market tool result. Captions retain axes, legends, reading notes and
chart-specific values. The starting context includes the current news report, UTC
run-start time, next three permanent reviews in UTC and IST, and scheduling context.
`previousRun` contains the full parent report only when a parent requested this review.

Main tools are the asset market tool, `show_available_strategy`,
`select_strategy_and_time` and `scheduled_next_agent_run`. Main agents have no calculator
or exit-preview tool. Selection calculates a seven-hour exit, shortened by the saved
expiry cutoff. Only `new_york_session` exposes an optional `holding_hours` choice of 7
or 11. Selection checks the saved version, current snapshot, contract availability and
session policy before publishing. Backend follow-up limits remain enforced.

Activation recheck receives two price charts, fresh market evidence, the selected
strategy and original report, fresh selected-option evidence, and overview history
restricted to the assigned expiries. It evaluates actual premiums, selected-leg IV,
fees, Greeks, depth, expiry payoff and local early-exit estimates. Overview ATM IV
never substitutes for selected-leg IV.

Recheck tools are `preview_selected_strategy`, `approve_strategy`, `drop_strategy` and the calculator.
The preview takes no arguments and refreshes only the assigned contracts. Initial
quote failure prevents model invocation; failed refresh invalidates evidence until
recovery. Approval requires currently valid option evidence. Both decision tools take
the assigned strategy name, activation time and a brief reason. The writer validates
the assignment and preserves the first committed decision on retries. Only a successful
approval permits entry; a successful drop cancels it. Reports and read-only tools cannot
approve. A run with no committed decision fails without approving entry. Recheck cannot replace the
strategy or change its schedule. The backend schedules it seven minutes before entry
and rejects entries that leave insufficient time.

Normalized previews are not account sizes. Expiry payoff is gross and applies at expiry;
Greek scenarios are estimates, not forecasts or guaranteed profit. Missing depth remains
unknown rather than becoming a new mandatory gate.

## Collection and retention

Existing market containers collect Delta options every 30 seconds and Binance Futures OI,
mark/index prices, funding and funding intervals every 60 seconds. Product and funding-interval
metadata is cached for five minutes. Clients reuse connection pools, limit concurrent public
requests to four, and honor cooldowns after 418/429 responses.

SQLite adds minute_candles, option_observations, futures_observations, liquidity_summaries, and
watched_expiries tables. Existing observation/history APIs remain available. Completed minute
bars are saved as they arrive. Option/futures evidence and liquidity summaries are saved at
ten-minute boundaries with actual source timestamps. No unavailable history is backfilled.
All observation tables keep 90 days; expired watch entries are pruned. SQLite reuses freed pages.

Option history includes every strike through 30 days and later expiries referenced by active,
scheduled, or previewed strategies. The backend publishes aggregate expiry dates using
X-Analysis-Secret. The market containers receive this service secret, not account balances or
exchange credentials. Settlement timestamps come from exchange product metadata rather than
an inferred time in a symbol name.

Liquidity samples run once per second. Summaries retain average/worst spread, average/minimum
depth, imbalance, traded flow and sample coverage. Price bands are 0.1%, 0.5%, and 1% around the
midpoint. Coverage is constrained by the original synchronized book bounds; isolated outer
updates do not prove that intervening depth is known. Full tick books are not archived.

Futures venues are independent. Missing/stale measurements are omitted without warning prose
or an automatic confidence adjustment. Selected quotes must be fresh; absent depth produces
unknown advisory fill estimates and does not create a new entry-blocking rule.

## APIs and rollout

Existing frontend APIs retain their shapes. Additional per-asset routes are:

- GET /api/market/{btcusd|ethusd}/agent-summary, optionally with assigned expiryDates.
- GET /api/market/{btcusd|ethusd}/option-catalogue for private numerical strike resolution.
- POST /api/market/{btcusd|ethusd}/selected-contracts, a bounded read-only quote/depth request.
- POST /api/market/{btcusd|ethusd}/watch-expiries, requiring X-Analysis-Secret.

Deploy the market containers and analysis service from the same repository revision.
The existing evidence routes are required for both main analysis and recheck; there
is no flag to enable or disable them. Supply both Compose env files when interpolating
the backend service secret. Run the offline input diagnostic before deployment:

```sh
docker compose --env-file .env.local --env-file backend/.env up -d --no-deps binace binace-eth
```

`backend/scripts/agent_input_dry_run.py` exercises frozen evidence without trading.
Its `run --offline` command constructs inputs without contacting a model. Optional
`--asset` and `--stage` filters select BTC/ETH or main/recheck. There is no path filter.
The capture command reads current market evidence and requires an existing news file;
it does not launch fresh news research.

## Offline input checks

The dry-run harness uses frozen evidence, a frozen clock, existing news and the
production chart renderers. Sessions and action clients use in-memory implementations;
snapshot/chart persistence is disabled. Publishing, scheduling and cancellation are
simulated, and no trading client is instantiated. `--offline` also disables model calls.

Results report text size, image count, tool registration, collection time, and model
usage when a model is invoked. Provider failures are recorded as failures. Compare
complete runs rather than treating starting text size as total model usage.

## Historical input-mode measurements

The measurements below describe the earlier September 30 input configuration. Its multi-expiry
overview and six holding scales were replaced on October 8 by the upcoming-session policy.

The final paired DeepSeek checks used frozen public market evidence and the same existing news
summary. All actions were simulated. The numerical results are in
[compact-agent-input-benchmark.json](compact-agent-input-benchmark.json).

| Main run | Initial input tokens, legacy → compact | Total input tokens, legacy → compact | Tools, legacy → compact | Cost USD, legacy → compact |
| --- | --- | --- | --- | --- |
| BTC | 11,099 → 13,945 | 103,204 → 110,156 | 4 → 2 | 0.0293 → 0.0504 |
| ETH | 11,199 → 11,863 | 91,367 → 101,102 | 7 → 2 | 0.0291 → 0.0477 |

Both final compact main runs used only preview_strategy and select_strategy_and_time. Images
dropped from six to three. Capture took approximately 3.6 seconds for BTC and 3.2 seconds for
ETH, including selected-contract evidence. Total main input grew approximately 7% and 11%.
At unchanged max reasoning, output/reasoning tokens and cost increased, and complete runs took
approximately 133/125 seconds versus 97/100 seconds. These are individual paired observations,
not a latency guarantee or evidence of trading profitability. There is no hard token cap.

Space Bunny was used only for interim development checks. Its recheck runs completed, while
main runs returned upstream SSE/empty-response errors; those were recorded as failures rather
than successful benchmarks. Live strategy and news models remain unchanged.

## Updated guardrails

News responses no longer require six exact headings or reject content containing DSML markup.
The completed-response, non-empty text, and provider-error checks remain. News instructions
still ask for relevant sections and linked evidence, with flexible section names and order.
Tool-call limits are 50 for news, 48 for main analysis, and 16 for activation rechecks.
Rechecks use sixteen calls. Per-tool research budgets and deadlines are unchanged.

Option unit, payoff and IV interpretation instructions apply to recheck. Main analysis receives
Spot/news guidance and is instructed not to infer current option value from those inputs.
Saved mandatory-gate and news-coverage guidance remain applicable to both stages.
