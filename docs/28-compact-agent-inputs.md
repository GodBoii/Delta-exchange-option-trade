# Compact market inputs and focused tools

`CURATED_AGENT_INPUT_ENABLED=true` enables the compact path for both BTC and ETH main agents and
activation rechecks. It defaults to false for staged deployment. Model IDs, reasoning settings,
news research and its summary handoff, saved strategy rules, account sizing, scheduling, stops,
and order execution policies remain unchanged. There is no event-calendar integration.

## Input and tools

As of October 11, 2026, live option analysis belongs to activation recheck. Main analysis
receives Spot indicators, dated session history, available historical baselines, flow,
Spot liquidity, separate futures context on the compact path, news and saved strategy rules.
It receives three price images on the compact path. Recheck receives two price images.
Chart captions contain axes and reading notes without duplicating the numerical summary.

The main catalogue retains run-local references, descriptions, saved leg rules, stops,
targets, size policies and order rules. It no longer contains indicative premium credits,
quote spreads or short-strike-distance comparisons. Main market input has no option
expiry overview, IV movement scales or option unit fields.

Main agents select a saved strategy and entry time with `select_strategy_and_time`.
The backend calculates a seven-hour exit, shortened by the saved expiry cutoff.
Only `new_york_session` exposes an optional `holding_hours` choice of 7 or 11.
Other reviews omit that field from the model's tool schema. The model no longer
calls `calculate_exit_time` before selection. Selection checks the saved version,
current snapshot, contract availability and session policy directly before publishing.
Private option evidence remains stored for resolution and audit.

Main action tools are `select_strategy_and_time` and `scheduled_next_agent_run`.
The legacy path additionally exposes `show_available_strategy` and its asset market tool.
Optional calculator tools remain on the compact path. Every main run receives its UTC
start time and the next three permanent reviews in UTC and IST, calculated by the
same timezone-aware fixed-session scheduler. `previousRun` contains only the parent
review that requested the current follow-up, including its full report; unrelated
recent decisions are not supplied. The backend schedules activation recheck seven
minutes before entry and rejects entries that leave insufficient time.
Follow-up limits remain backend-enforced.

Both legacy and compact rechecks receive a fresh preview for their assigned strategy.
It includes selected-contract premiums, multipliers, IV, spreads, depth fill estimates,
normalized credit/debit, fees, Greeks, gross expiry payoff and local early-exit estimates.
Selected-leg IV scales use the assigned entry-to-exit duration. The compact recheck also
receives overview history restricted to its assigned expiry dates, including older proposals.
It must use selected-leg IV for the actual trade rather than substitute overview ATM IV.

`preview_selected_strategy()` refreshes that assigned preview. It has no arguments and
cannot change strategy, asset, entry, exit or saved rules. It is available only to recheck.
Fresh quotes must satisfy the existing 30-second observation limit and match asset,
option type, strike and expiry. An initial evidence failure fails the recheck before a
model runs. A failed refresh invalidates evidence until a successful retry; it cannot
become automatic approval. The agent can cancel instead.

Recheck tools are `preview_selected_strategy`, `drop_strategy`, and add, subtract,
multiply, divide, exponentiate and square_root. Both input paths allow sixteen calls.
Only `drop_strategy` is a cancellation attempt. Calculator and preview calls do not cancel
or prevent backend approval when evidence is valid. A failed drop is never reconfirmed.
Recheck cannot select a replacement strategy or alter the assigned schedule. Its seven-minute
lead time, model, low reasoning effort and execution/allocation responsibilities stay the same.

Payoff and scenario calculations retain their existing units and limitations. Normalized
preview size is not account size. Expiry payoff is gross and does not describe an earlier
exit. Greek scenarios are local estimates, not forecasts or calibrated probabilities.
Missing depth remains an unknown advisory estimate, not a new mandatory liquidity gate.

Raw candles, trades, books and complete chains stay outside model context. Private snapshots
and tool records retain evidence for audit. Earlier reports remain intact as dated context;
they can contain historical option commentary, but are not fresh option measurements.
There is no hard token cap. Display numbers use eight significant digits while stored
and execution definitions retain precision.

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

Deploy market containers first, with the backend's ANALYSIS_SERVICE_SECRET supplied explicitly
through Compose interpolation. Run the paired dry run before enabling the analysis-service flag.
The two Compose env files must be supplied when interpolating the backend service secret:

```sh
docker compose --env-file .env.local --env-file backend/.env up -d --no-deps binace binace-eth
```

Set the flag in backend/.env for the analysis service and restart that service after validation.
The backend also needs the updated aggregate-watchlist publisher. Disabling the flag uses
the legacy Spot input and catalogue tools. It does not restore
live option analysis to the main agent; assigned option previews remain on recheck.

## Paired dry run

`python -m scripts.agent_input_dry_run capture cases.json --btc-url ... --eth-url ... --news news.md`
freezes existing market-service evidence and an existing news summary. It does not run news research.
`python -m scripts.agent_input_dry_run run cases.json results --offline` checks input construction;
omit --offline to invoke the configured models. Optional --asset, --stage, and --path filters
allow independent cases to run in separate processes.
Use `--model stealth/space-bunny-alpha` for interim development checks while its pricing is free.
Omit --model for final verification with the configured DeepSeek model. This override only clones
dry-run settings and never changes the live strategy or news-agent models.

Both paths use the same frozen evidence, a frozen simulation clock, news, strategy universe,
model, and reasoning setting. Charts use the production renderers. Sessions and action clients
are replaced with in-memory implementations; snapshot/chart persistence is disabled. Every
publishing, scheduling, and cancellation outcome is simulated. No trading client is instantiated.

Results include initial/total input tokens, output/reasoning tokens, provider cost when available,
tool names/arguments/result bytes/duration, image count, collection latency, and total duration.
Provider failures are marked failed, not counted as successful model comparisons. Compare
complete runs, because repeated tool rounds and image costs are not captured by text byte counts.

## Measured validation

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
Tool-call limits are 50 for news, 48 for main analysis, and 16 for compact rechecks.
Both recheck input paths now use sixteen calls. Per-tool research budgets and deadlines are unchanged.

Option unit, payoff and IV interpretation instructions apply to recheck. Main analysis receives
Spot/news guidance and is instructed not to infer current option value from those inputs.
Saved mandatory-gate and news-coverage guidance remain applicable to both stages.
