# Compact market inputs and focused tools

`CURATED_AGENT_INPUT_ENABLED=true` enables the compact path for both BTC and ETH main agents and
activation rechecks. It defaults to false for staged deployment. Model IDs, reasoning settings,
news research and its summary handoff, saved strategy rules, account sizing, scheduling, stops,
and order execution policies remain unchanged. There is no event-calendar integration.

## Input and tools

The starting input contains compact JSON for Spot indicators, dated session history, historical
baselines when at least 144 observations exist, windowed flow, liquidity, separate Delta/Binance
futures context, an option-expiry overview, and the saved-strategy catalogue. It includes three
price images for main analysis and two for recheck. Chart captions carry axes and reading notes;
their numerical statistics are not copied into another market-data section.

The catalogue supplies run-local references and decision-relevant saved rules. Its indicative
cost/spread/short-distance comparisons use one normalized saved-leg ratio and the first eligible
upcoming session expiry. They help rank strategies before requesting a final preview.
Account trade size is still calculated by the trading engine at entry.
The starting input supplies indicative scales for the seven-hour preset, plus eleven hours only
for the evening review. Both use the upcoming expiry and shorten near its default five-minute
buffer. Missing IV stays unknown. The successful preview supplies selected-leg IV scales using
the actual duration and saved buffer, which supersede these indicative starting comparisons.

`preview_strategy(strategy_ref, activation_time, exit_choice)` replaces the catalogue and exit
lookup tools on the compact path. `exit_choice` is an Agno/Pydantic schema whose discriminator
is `kind`, with hours or exit_at. Only intraday and bounded specific_time choices remain. The tool refreshes selected
contracts, resolves the exact schedule and strikes, and provides gross expiry payoff, breakevens,
bounded/unbounded outcomes, fee estimates excluding tax when available, and advisory depth fills.
Limit-order fill estimates respect the saved limit price. The selection must match a successful
preview and the current saved-strategy version and analysis snapshot.

Fixed pre-expiry estimates show separate Spot moves of ±0.5%/±1%, IV changes of ±5 points, and
one hour of time decay. Delta/gamma are weighted by signed contract exposure. Vega and theta
scaling is checked against Greek identities using the quoted IV and remaining time. Inconsistent
or unavailable values remain unknown. These are local value-change estimates, not price forecasts.
IV-scaled movement in the expiry overview is `IV × sqrt(days / 365)`, not a calibrated probability.

The remaining action tools are `select_strategy_and_time`, `scheduled_next_agent_run`, and
`drop_strategy` for the assigned recheck only. Agno CalculatorTools exposes add, subtract,
multiply, divide, exponentiate, and square_root. Rechecks permit sixteen calls so calculation can
precede cancellation; they cannot choose or schedule another strategy. Their lead time remains
seven minutes. Provider-error responses fail the compact path rather than becoming a valid review.

Raw candles, trades, depth ladders, complete option chains, and complete saved definitions stay
outside LLM context. Decision snapshots still retain the underlying evidence for audit/replay.
There is no hard input-token cap; numerical displays use eight significant digits while stored
evidence and private execution definitions retain their precision.

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
The backend also needs the updated aggregate-watchlist publisher. Disabling the flag restores
the original agent inputs/tool set without removing collected history or changing execution policy.

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
The legacy recheck keeps its one-call limit. Per-tool research budgets and deadlines are unchanged.

Main and recheck agents share five interpretation instructions: distinguish quoted premium from
contract/strategy-unit cost; separate expiry payoff from early-exit valuation; treat realized/IV
comparisons as evidence rather than guaranteed edge; reserve mandatory-gate language for enforced
rules; and qualify catalyst statements by the supplied news coverage. These change interpretation
and reporting, not strategy definitions, tool calculations, sizing, or execution policy.
