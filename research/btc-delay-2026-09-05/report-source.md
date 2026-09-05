# BTC delay monitor for Delta Exchange

Research, code review and implementation plan. Prepared September 5, 2026 for the owner of Delta Strategy Desk.

## Decision

Select four coins for prospective monitoring: BONK, PEPE, SHIB and WIF. Use DOGE as a liquid comparison market and FLOKI as an excluded research control. None of these coins currently qualifies for automatic live trading on the evidence collected here.

This selection follows a new diagnostic using actual Delta historical candles. A simple BTC shock strategy produced positive average five-minute candle returns for those four coins in July 2026. Comparable Binance contracts did not show the same effect. That difference makes Delta price adjustment worth investigating. It also raises the possibility that infrequent trades, candle construction or venue-specific pricing created the apparent delay. Only recorded executable quotes can settle that question.

Implement a separate event-driven monitor and recorder first. Its initial job is to determine whether a price advantage remains at Delta's executable bid or ask when our server receives the BTC signal. A future trading component should enable each coin and direction individually after qualification. The active set may contain fewer than four coins, including zero.

Scope: Delta India perpetual futures for execution; public Binance BTC data as a potential predictor, with ETH, SOL and matching meme instruments as controls. Reviewed the referenced task, "Find Delta Exchange crypto products", and the current repository. Production implementation and account changes are outside this deliverable. Research scripts, raw public data, a workbook and this report are retained in the research folder.

## Evidence and corrections to the earlier answer

The closest primary study, Kurihara and Matsumoto, published March 10, 2026, found minute-scale delays in selected small altcoins. Its cases were QKC, BIFI, CITY, GNO and PIVX, rather than the memes selected here. ETH and LTC mainly moved simultaneously. Its model alternated between long and flat positions; it did not establish a BTC-down-to-meme-short strategy. The study used selected event windows, brief forward tests and a 0.02% fee assumption. Its stated return formula does not include executable spread or market impact. It supports further measurement, with limited evidence for transfer to Delta. [Price Transmission from Bitcoin to Altcoins, Springer Nature, 2026](https://link.springer.com/article/10.1007/s10690-026-09589-z)

A separate study of BTC, ETH, DOGE and SHIB found changing and bidirectional spillovers at four-hour frequency. This supports testing bullish and bearish relationships separately; it does not establish an intraday execution window. [Li and Yang, Finance Research Letters, 2022](https://www.sciencedirect.com/science/article/pii/S154461232200397X)

My earlier recommendation to research WIF first was too strong. It was not based on a validated Delta delay. The earlier symbol filter also missed scaled contracts. Current product metadata includes 1000PEPEUSD, 1000BONKUSD, 1000SHIBUSD and 1000FLOKIUSD alongside DOGEUSD and WIFUSD. Availability and correlation should never be reported as demonstrated profitability. [Delta public product catalog, accessed September 5, 2026](https://api.india.delta.exchange/v2/products)

## What I measured

Downloaded 18 Binance USD-M monthly archives for BTC, ETH, SOL and six memes, covering June and July 2026. Checked every archive against Binance's published SHA-256 checksum and verified contiguous timestamps and positive prices. This is 790,560 instrument-minute observations. The archives are first-party data, not an aggregated chart vendor. [Binance public-data documentation](https://github.com/binance/binance-public-data)

Downloaded 150 bounded Delta candle windows for the same six memes in July, using the public historical candle endpoint. Requests used nonoverlapping windows smaller than the response cap. Duplicate timestamps and invalid prices were rejected. Missing candles were not filled forward. Raw responses and request URLs are retained.

The diagnostic rule was fixed before examining July results:

1. Calculate absolute BTC one-minute log returns in June. Use the 99th percentile, 26.4058 basis points, as the shock threshold.
2. During July, take the direction of a BTC return that exceeds that threshold. Suppress further signals for seven intervening minutes, so signals are at least eight minutes apart.
3. Measure a five-minute same-direction return from the next candle's open. Also test one additional minute of entry delay.
4. Use only Delta events with all six minute timestamps present between entry and exit. This avoids silently spanning gaps.
5. Analyze bullish and bearish events separately as well as together. There were 78 BTC shocks, 43 bullish and 35 bearish. Some Delta coins had fewer eligible events because of gaps.

Candle opens are descriptive trade-price proxies. They are not executable quotes and their first trade need not occur exactly on a minute boundary. This is an exploratory screen, not a fill simulation or a deployment-grade out-of-sample backtest. June was used only to calibrate the threshold; no complete model was trained or validated over repeated forward folds. The current universe also introduces selection and survivorship limitations.

### Results at Delta

Average return per eligible event, in basis points. One basis point is 0.01%. The fee-only column subtracts 11.8 bps as an equal-notional current-fee sensitivity; it omits actual historical spread, slippage, funding and taxes on profits.

| Contract | Events | Gross, immediate proxy | After fee-only sensitivity | Gross, extra 60s delay | Gross 95% interval |
| --- | ---: | ---: | ---: | ---: | --- |
| 1000BONKUSD | 78 | +21.78 | +9.98 | +18.53 | +9.70 to +36.92 |
| 1000PEPEUSD | 78 | +14.80 | +3.00 | +7.52 | -0.76 to +31.99 |
| 1000SHIBUSD | 77 | +13.69 | +1.89 | +11.96 | +3.72 to +25.94 |
| WIFUSD | 76 | +12.68 | +0.88 | +10.43 | +2.17 to +28.65 |
| 1000FLOKIUSD | 71 | +5.83 | -5.97 | +4.44 | -2.97 to +17.41 |
| DOGEUSD | 78 | -4.34 | -16.14 | -2.78 | -9.50 to +1.08 |

Intervals use 5,000 resamples of whole UTC days from July. They are unadjusted for testing several coins and diagnostics. Even BONK's lower gross bound is below the 11.8 bps baseline fee sensitivity. They provide no statistically established profit after full costs. This short, single-month test cannot establish consistency through changing regimes.

Observed candle coverage was 99.95% for DOGE, 99.30% for PEPE, 99.01% for SHIB, 99.03% for WIF, 98.92% for BONK and 92.28% for FLOKI. Missing bars may reflect absent trades or feed/history gaps; the endpoint alone does not distinguish them. Eligible-event filtering can bias a thin-market sample. Absence of a candle does not imply absence of executable quotes.

The directional split is informative. BONK's gross means were +21.74 bps after bullish BTC shocks and +21.84 after bearish shocks. PEPE showed +16.08 and +13.24. SHIB showed +21.72 and +4.05, suggesting that its bearish case is much weaker. WIF showed +10.88 and +14.80. These are descriptive results from only 32 to 43 observations per direction, not stable hit rates or approved trading rules.

### Why the venue comparison matters

On Binance, the same immediate-entry diagnostic had negative gross means for every tested meme: DOGE -4.38, PEPE -5.41, SHIB -2.23, WIF -7.33, BONK -2.08 and FLOKI -4.85 bps. Their same-minute correlations with BTC ranged from 0.41 to 0.74. Five of six next-minute correlations were slightly negative. FLOKI's small positive next-minute correlation disappeared in the incremental BTC regression controlling its own return, ETH and SOL.

The contrast is consistent with venue-specific delay, but it does not prove it. A crucial test is whether the same meme's Binance quote already explains the next Delta quote change. If so, BTC is not the independent predictor we thought it was. Another possibility is that Delta's last trade is old while its order book has already moved. The recorder must capture both markets' quotes, and replay information by local arrival time.

## The four-coin monitoring shortlist

Current liquidity snapshot collected September 5, 2026 at 03:47:48 UTC, 09:17:48 IST. Turnover and open interest are exchange-reported. Spreads are derived from best bid and ask. This is a momentary snapshot, not an average or a capacity estimate.

| Role | Coin and Delta contract | Full spread, bps | 24h turnover, USD | Open interest, USD | Research reason |
| --- | --- | ---: | ---: | ---: | --- |
| Candidate | BONK, 1000BONKUSD | 12.28 | 23,713 | 20,298 | Largest observed Delta gross response; very thin trading |
| Candidate | PEPE, 1000PEPEUSD | 10.97 | 220,126 | 67,267 | Stronger turnover among the delayed candidates; timing sensitive |
| Candidate | SHIB, 1000SHIBUSD | 5.71 | 45,547 | 36,746 | Tighter sampled spread; bullish effect stronger than bearish |
| Candidate | WIF, WIFUSD | 9.86 | 34,837 | 29,637 | Positive Delta candle response; limited turnover |
| Comparison | DOGE, DOGEUSD | 3.54 | 5,644,912 | 935,097 | Best liquidity here, but negative measured simple-rule response |
| Exclude initially | FLOKI, 1000FLOKIUSD | 19.83 | 50,058 | 8,789 | Wide spread, weak response and most missing candles |

[Delta perpetual ticker snapshot source](https://api.india.delta.exchange/v2/tickers?contract_types=perpetual_futures)

I would record all six because comparison data are inexpensive. The primary delayed-response research set contains four. The execution-quality priority starts with PEPE and SHIB; the largest apparent gross response is BONK. Those are different rankings. A momentary spread should not decide permanent membership.

## Can the observed response pay the costs?

Delta currently publishes futures taker fees of 0.05% and maker fees of 0.02%, plus 18% GST on fees. Approximately equal entry and exit notionals imply 11.8 bps for a taker round trip. Exact accounting must use each fill's notional and actual commission. [Delta fee schedule, accessed September 5, 2026](https://www.delta.exchange/fees)

For illustration only, subtracting that fee plus one current full spread from the July gross means gives BONK -2.30, PEPE -7.97, SHIB -3.82 and WIF -8.98 bps. One full spread approximates crossing half a spread at entry and half at exit when spreads and notional are similar. The September snapshot is not July execution data. This stress test demonstrates how small the margin is; it is not a historical net-return estimate.

Delta also has an opt-in closing-fee offer for qualifying quick exits. For these futures the stated holding window is 15 minutes. A qualifying taker entry could therefore cost approximately 5.9 bps including GST, before spread and other costs. The offer can change and subaccounts need their own eligibility. We have not checked your account or enrolled it. Under this illustrative scenario, BONK and SHIB retain small positive means before slippage and funding, but their confidence bounds still do not establish a full-cost edge. [Delta scalper offer](https://www.delta.exchange/support/solutions?articleId=80001172745)

Funding must be charged at the actual settlement snapshot, with product-specific intervals and the correct rate unit. Short holding periods do not make it disappear. [Delta funding mechanism](https://www.delta.exchange/support/solutions?articleId=80001199725)

Income tax treatment is outside this screen. No reported figure is account profit, compounded portfolio return or annualized return.

## Codebase findings

Static inspection covered the feed, REST clients, strategy schemas, scheduling, entry and exit flow, capital reservation, relevant tests and status publication. I did not invoke the live engine or authenticate to Delta. No production files changed.

| Finding | Evidence in repository | Implication |
| --- | --- | --- |
| Existing feed is BTC Spot only | binance_backend/app/feed.py:129 and config.py:12 | Add dedicated multi-symbol adapters for the new service |
| Dashboard publish coalesces events | feed.py:273, 250ms default; subscriber queue size two | Fine for display; unsuitable as the research event source |
| Delta context is REST-polled | config.py:15, default five seconds | Use direct execution-venue book streams |
| Freshness is shared across channels | feed.py:163 and status at 328 | A fresh trade/ticker can mask old depth; track per-stream health |
| Current local book has synchronization checks | feed.py:190 and 214; tests/test_feed.py:13 | Reuse lessons and fixtures, with venue-specific sequence rules |
| Models describe BTC/ETH options | backend/app/models.py:14 and 32; lib/strategy-types.ts:44 | A perpetual delay strategy needs a separate domain model |
| Entry is a REST/database workflow | backend/app/engine.py:679 | Avoid using it directly for short-lived signals |
| Order timeout is generalized | backend/app/delta.py:19 and 57 | Add explicit unknown-order state and reconciliation |
| Capital reservations are strategy-scoped | engine.py:361; migration 010:87 | New engine cannot independently spend the same balance |
| New connections call an obsolete endpoint | backend/app/auth.py:100; delta.py:69 | Review onboarding before future live integration |

The trading scheduler defaults to two seconds. The AI automation scheduler defaults to thirty seconds, and the AI market tools collect REST snapshots. The AI team also plans scheduled option entries rather than making tick-level decisions. These are architectural mismatches for the proposed monitor, not reasons to speed up every existing component.

There is a material order ambiguity in the current entry path. If POST /orders times out after the exchange accepted it, the wrapper raises a generic error; execute_entry records failure and may release its reservation when no successful response was counted. An order might nevertheless exist. This is a statically identified scenario, not an observed account incident. The new executor must preserve uncertainty, reconcile and retain the reservation until exposure is resolved.

The current code has useful safeguards worth retaining: HMAC signing, server-side credential retrieval, integer reduce-only closing quantities, position-reduction verification, atomic database capital-slot reservation and some reconciliation tests. Existing exit verification polls every 500ms and has a ten-second timeout; that is not a promise of fast fills. New execution must be driven by private order/fill updates with REST repair.

## Proposed system

Use a separate service, provisionally named btc-delay-monitor. Start with Python asyncio, matching the existing stack. A six-coin recorder and compact numeric feature calculations do not justify a Rust rewrite before profiling. Model training, archive processing and PDF/dashboard work should run in separate processes. A Rust component becomes justified only if measured CPU or event-loop delay remains material after ordinary optimization.

The process relationships should be:

Binance BTC/ETH/SOL and matching meme quotes + Delta executable books -> adapters and timestamp validation -> local books and rolling features -> deterministic signal and risk evaluation -> durable intent -> Delta order submission -> private fill reconciliation.

An asynchronous recorder branches from the event stream. A small control API exposes health, configuration, paper results and later pause/disable commands. The dashboard consumes coalesced status. Neither the dashboard, an LLM nor MCP belongs in the per-signal order path.

Delta MCP can support human research and account inspection. Its tools wrap market/account/trading operations; direct documented sockets and REST provide the better interface for this persistent service. [Delta MCP tool reference](https://mcp.delta.exchange/docs/tools)

### Feed contracts and protocol versioning

Current Delta public data use public-socket.india.delta.exchange with ob_updates/trades. Book updates are published at 100ms; ticker at five seconds. Rebuild after sequence gaps and verify the top-ten CRC32. Private orders use the private socket. Delta documents AWS Tokyo hosting, five-second signature validity, and client IDs unique across open orders only. Its changelog says API-key access to GET /v2/profile ended August 19, 2026. Legacy public channels were scheduled for removal July 31. [Delta API reference and changelog](https://docs.delta.exchange/)

Binance USD-M uses routed public sockets for individual bookTicker and depth, and market sockets for aggTrade. Individual bookTicker is real-time; all-symbol bookTicker is five seconds. aggTrade batches at 100ms. The adapter must observe documented connection lifetimes, limits and keepalives. Its depth synchronization differs from Spot, including the previous-update field. [Binance public streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public), [market streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market), [migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice)

Version each adapter. Save representative schema fixtures before implementation. Transport connected, book synchronized and trading data acceptable are separate states. Book silence can mean unchanged quotes, so use heartbeat, sequence integrity, an independent top-of-book comparison and quote-change timestamps together. A heartbeat alone does not prove the book is correct.

Capture exchange transaction time where supplied, publication time, local UTC receipt time and local monotonic receipt time. Preserve units explicitly. Calibrate clock offsets, monitor NTP drift and never infer one-way network latency by blindly subtracting clocks from different venues. Replay by local receipt order to determine what our system could actually know.

### The signal

Evaluate BTC price changes over a small preregistered grid, for example 1s, 5s, 15s and 60s. Candidate future holding periods might be 1s, 5s, 15s, 30s, 60s and 300s. These are proposed research settings, not discovered optimal parameters. Count every combination in the search family.

Learn separate bullish and bearish models using trailing volatility, BTC return, trade flow, the meme's own returns, its matching Binance price, ETH/SOL returns, quote age, spread and depth. Keep all feature calculations causal. A high rolling beta or a price that has moved less than beta predicts is only a feature; it does not itself establish an uncompleted price response.

The core condition is that a conservative estimate of future signed executable return exceeds entry/exit fees, expected spread, size-dependent impact, latency loss and funding, with a model-uncertainty margin. Estimate these from earlier recordings. Do not subtract spread twice when the target already measures ask-to-bid or bid-to-ask returns.

Signals expire. Recheck the current book and predicted edge immediately before dispatch, including after waiting for intent durability. Deduplicate all candidates from one BTC shock into a single risk event. Prefer the best eligible candidate initially rather than opening four correlated bets at once. A fresh symbol-specific event can invalidate the model even if BTC direction remains unchanged.

### Execution and risk ownership

Use one serial execution owner per exchange account, persistent HTTP connections and prevalidated product metadata. Persist the intent and its deterministic ID before dispatch. Keep the statistical computation fast, but never trade away recovery guarantees for a latency target.

Represent states explicitly: observing, eligible, intent durable, submitting, submission unknown, working, partially filled, filled, exiting, flat and halted. Private updates can arrive before REST acknowledgement; merge them by exchange order ID and event identity. A timeout is not a rejection. Lookup by client ID, order history, fills and position before deciding whether to retry. Reusing a client ID is not an exactly-once guarantee once the earlier order is filled.

For entry, test a marketable IOC limit with a maximum acceptable execution price. An unfilled or partially filled order is a valid outcome, not a reason to chase indefinitely. Create verified protection for the actual filled quantity, close with reduce-only orders, and reconcile residual exposure. On protection failure, follow a tested emergency exit policy. Stops limit intended loss but cannot guarantee execution price in a gap.

Reserve rate-limit capacity for exits and reconciliation. A full book-delta queue invalidates the affected book and suspends entries. A lagging audit writer likewise prevents new entries when durability cannot be guaranteed. Display updates may be dropped or coalesced. Book deltas, order events and unresolved intent records must not be silently discarded.

Before future live use, choose either a separately funded Delta subaccount with exclusive ownership, or a genuinely shared account risk authority used by both engines. A separate API key alone does not isolate balance, margin or positions. Subaccount creation/funding is not performed here. For a shared account, both paths must honor atomic budget reservations, external orders, aggregate BTC exposure and product ownership. The present strategy-slot table cannot be treated as an unlimited second budget.

Use a single active executor with a fenced ownership lease. Failover must prove the previous executor cannot submit before taking over, then reconcile the exchange. A database lease alone cannot stop a partitioned old process from using a still-valid API key. Initially favor restart-and-reconcile over automatic active/active trading.

### Contract sizing

The catalog confirms these linear, non-quanto USD quoted and settled contracts. Revalidate metadata whenever it changes.

| Contract | Contract value | Unit | Underlying tokens per contract |
| --- | ---: | --- | ---: |
| DOGEUSD | 100 | DOGE | 100 |
| WIFUSD | 1 | WIF | 1 |
| 1000PEPEUSD | 1000 | 1000PEPE | 1,000,000 |
| 1000BONKUSD | 1000 | 1000BONK | 1,000,000 |
| 1000SHIBUSD | 1000 | 1000SHIB | 1,000,000 |
| 1000FLOKIUSD | 100 | 1000FLOKI | 100,000 |

For these contracts, quote notional is contract count times contract value times the quoted product price. For example, PEPE quoted at 0.00346 and contract value 1000 implies about USD 3.46 notional per contract. Do not multiply by the 1000 token prefix again in this notional formula. The prefix matters when converting to an unscaled spot price or physical token count.

Size by allowed loss and liquidity, not the exchange's maximum gearing. Divide the risk budget by stop distance times contract value plus stressed per-contract costs; then cap by available margin, permitted notional and executable depth. Round down to whole contracts, apply tick increments to prices and reject a size below the permitted minimum. Track portfolio exposure jointly across all memes and existing strategies. No live risk percentage is chosen by this report.

### Latency and deployment targets

Benchmark an always-on Linux service in AWS Tokyo first because of the documented venue location, and compare practical alternatives. This is an experiment to select hosting, not a claim of measured network performance. Avoid desktop tunnels and serverless cold starts for execution.

Initial engineering targets, to be measured under captured burst replay:

| Stage | Proposed p99 target | Interpretation |
| --- | --- | --- |
| Receive, decode, validate, update features | Below 5ms | Local compute only |
| Durable intent acknowledgement | Below 10ms | Local durable storage, never skipped |
| Receipt to submit, including risk and intent | Below 20ms | No remote database lookup per signal |
| Feed publication, network, acknowledgement and fill | Measure separately | No latency guarantee from this research |

Report p50, p95 and p99 separately for receive-to-decision, decision-to-send, send-to-ack and send-to-fill. Exchange publication batching already consumes part of the opportunity. A successful design requires the observed residual advantage to survive its whole latency distribution, especially the tail. If it exists only in stale candle data or disappears before orders arrive, faster local code will not rescue the strategy.

Record raw events into bounded, batched storage, with sequence and configuration/model versions. Start with compressed append-only segments and a local durable intent journal. Export analytic data to Parquet, queried with DuckDB; use the existing application database for configuration and aggregate results. Add a separate writer process if profiling shows stalls. Kafka, Kubernetes and a new distributed database are unnecessary for this initial scale. Set retention by measured bytes per second and replay needs, not by an invented storage estimate.

## Implementation sequence

These are future implementation tasks. Only the research tools and artifacts exist today.

| Phase | Concrete work | Completion gate |
| --- | --- | --- |
| 1. Contracts and recorder | New btc_delay_backend service skeleton, versioned adapters, product registry, raw capture, data-health metrics | Recorded schema fixtures; clocks and sequence repair tested; no trade credentials needed |
| 2. Replay and qualification | Receive-time replay, executable quote targets, causal features, rolling forward tests, cost and latency curves | Signed per-coin/per-direction evidence report; allow zero qualifiers |
| 3. Shadow monitor | Live deterministic signals, recorded reasons, paper fill model, replay/live parity | Prospective evidence across enough independent events and different market conditions |
| 4. Execution preparation | Account ownership, durable intents, private-stream reconciliation, IOC/partial-fill/protection logic | Ambiguous order, crash and duplicate-signal scenarios pass in isolated testing |
| 5. App integration | Authenticated settings/status API, result tables, risk coordination, dashboard status | Disabled by default, no dependence on browser availability |
| 6. Controlled activation | User-approved risk budget, eligibility and execution settings; only qualified coin/directions | Measured live fill quality remains within research bounds; immediate disable on model/data failure |

Suggested file responsibilities in the future service: adapters/binance.py, adapters/delta_public.py, adapters/delta_private.py, instruments.py, books.py, features.py, signal.py, risk.py, execution.py, journal.py, recorder.py, replay.py, metrics.py and api.py. Keep each file tied to a behavior; do not create generic managers or duplicate the existing options model. Use typed, validated events and integer ticks/contracts at order boundaries.

Add a new service to Docker Compose only during implementation. New configuration/result tables should store monitors, approved models, signals, intents, fills and account risk ownership. Use separate engine identifiers and authenticated user scopes. Proposed API routes are GET /api/btc-delay/status, GET /api/btc-delay/candidates, GET /api/btc-delay/signals, PUT /api/btc-delay/config and POST /api/btc-delay/pause. Config updates are versioned and audited; live enablement is a separate explicit action. Existing Convex status scopes would need an intentional extension or a dedicated endpoint. UI publishes should remain coalesced.

For one experienced engineer, a recorder and replay prototype might require roughly one working week, with execution/recovery and integration requiring another two to four weeks. These are planning estimates, not commitments. Prospective evidence may require four to eight weeks or longer, depending on independent events and regime coverage. Calendar time never substitutes for passing the statistical and execution gates.

## Qualification and verification

Compare the BTC model with no trade, the meme's own signal, matching-meme Binance-to-Delta pricing, ETH/SOL controls and a BTC trade sized to equal ex-ante volatility. Record whether BTC adds useful forecast information. Do not claim a BTC-specific discovery if the same-coin venue spread explains it all.

Use repeated chronological training/validation/test windows. Select coins, directions, thresholds, horizons, scaling and liquidity cutoffs within training/validation only. Freeze all choices for each forward window. Bootstrap whole shock episodes or days; account for testing multiple configurations. Selection bias makes the most attractive backtest unreliable without this correction. [Bailey and Lopez de Prado, Deflated Sharpe Ratio, 2014](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)

Require positive conservative net excess return after the declared search correction, stability across several forward folds and acceptable drawdown/size capacity. Then require prospective shadow results whose fills and latency assumptions agree with recorded books. Investigate a result dominated by one day, coin, event or fee promotion. A fixed number of paper-trading days is insufficient.

Essential behavior tests cover dropped/duplicate/out-of-order deltas, checksums, unchanged books, bad timestamps, feed interruption, burst overload, process restart, intent persisted without send, send accepted without response, fills arriving before acknowledgement, partial fills, failed protection, configuration rollback, funding boundaries, symbol scaling and competing capital reservations. Use protocol fixtures and fault injection. Do not exercise live accounts merely to test recovery.

Research QA completed: checksum validation for 18 Binance archives; timestamp/positive-price checks; bounded Delta requests; no forward filling; six independent Decimal recomputations of selected raw Delta events; source and calculation preservation in the workbook. Statistical tests are exploratory and unadjusted where stated. No end-to-end latency benchmark, historical order-book simulation, income-tax analysis or live trading verification was performed.

## Remaining uncertainty

The outstanding decision is whether the four observed Delta responses survive executable quotes after actual arrival and order latency. There is no honest fixed delay estimate per coin yet. A five-minute forward return does not imply a five-minute delay. Current spreads do not supply historical trading costs. The four candidates are a concrete research set, and automatic trading remains conditional on future measurements.

The literature search stopped after the most relevant primary study and its limitations were verified, current venue contracts and protocols were checked, and direct historical diagnostics were completed. More broad searches are unlikely to replace the missing Delta quote recordings.
