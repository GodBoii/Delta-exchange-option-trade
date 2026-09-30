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
expiry covering the next hour. They help rank strategies before requesting a final preview.
Account trade size is still calculated by the trading engine at entry.
The starting input also supplies six standard holding-period volatility scales. It uses the
nearest quoted expiry covering each hold and keeps unavailable IV scales unknown. These simple
scales avoid repeated calculator calls and are explicitly labelled as estimates, not forecasts.

`preview_strategy(strategy_ref, activation_time, exit_choice)` replaces the catalogue and exit
lookup tools on the compact path. `exit_choice` is an Agno/Pydantic schema whose discriminator
is `kind`, with the existing hours, exit_at, or expiry_number fields. The tool refreshes selected
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
multiply, divide, exponentiate, and square_root. Rechecks permit eight calls so calculation can
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
