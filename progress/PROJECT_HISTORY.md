# Trade Cognition / Delta Exchange Website — Complete Project History

Last consolidated: 2026-08-13

## 1. Scope and source of this record

This document consolidates the 25 prior Codex tasks currently associated with `C:\Users\prajw\Downloads\delta-exchange`. The histories were read at turn level, not inferred only from task titles. Repository documentation, the existing combined-stop progress log, recent Git history, and the current working tree were also inspected.

This is a historical record, not a claim that every old implementation still exists unchanged. The project evolved repeatedly. Important supersessions are called out so an old task result is not mistaken for the current architecture.

Security-sensitive values that appeared in earlier conversations are intentionally not reproduced here. API keys, database passwords, and service-role credentials that were pasted into chats should be treated as exposed and rotated.

## 2. What the website is now

Trade Cognition is a client-facing Delta Exchange India options strategy workstation with three backend services:

```text
Next.js frontend
  ├─ Supabase Auth and saved strategy library
  ├─ Strategy builder, dashboard, run history
  ├─ Market analysis UI
  └─ News intelligence UI

Docker services
  ├─ Delta-exchange: authenticated FastAPI trading API + scheduler
  ├─ Binace: public Binance Spot + public Delta market context
  └─ news-analyzer: private Agno/OpenRouter research service

Supabase
  ├─ Authentication
  ├─ Postgres + RLS
  ├─ Vault-backed Delta credentials
  ├─ Saved strategies and immutable execution runs
  └─ Agno news-agent sessions
```

The frontend can run in two modes:

- Backend-connected local mode: live Delta connection, contract preview, scheduling, execution, positions, orders, run history, live market analysis, and news analysis.
- Backend-optional design mode: authentication, saved strategy editing, browser recovery, and JSON import/export continue to work without the local trading backend.

The Vercel deployment is suitable for the frontend, but the live scheduler and Delta execution service require an always-on backend with a stable outbound IP. The frontend alone cannot provide reliable scheduled trading.

## 3. Architecture evolution

### 3.1 Initial application

The first version was built as a complete Next.js Delta strategy desk. It included session authentication, encrypted API credentials, Delta HMAC signing, option-chain resolution, custom legs, market and limit orders, bracket parameters, scheduled entry/exit, balances, positions, open orders, cancellations, and run history. The initial continuous scheduler was a separate Node process (`npm run worker`).

The original safety model used Testnet by default, a Production warning, and a typed `EXECUTE` confirmation. Later UI work removed that confirmation flow and converted the client-facing system to a server-configured production environment.

### 3.2 Supabase and client authentication

The application was migrated to Supabase for:

- Email/password authentication.
- Google OAuth through Supabase.
- Persistent application sessions.
- User profiles and user-scoped data.
- Strategies, executions, and order records.
- Row Level Security.
- Vault-backed Delta credential storage.

Google authenticates the user into Trade Cognition. It does not authorize Delta trading. Delta's public API does not provide a public OAuth/OIDC account-linking flow, so users still connect a Delta API key and secret once. The secret remains server-side.

The Google OAuth flow was documented and configured around Supabase as the broker. The Google redirect URI is the Supabase callback; Supabase then redirects to the application's `/auth/callback`. Test-mode Google accounts must be added under Google Auth Platform's test users.

### 3.3 Node worker replaced by Python/Docker

The Node trading routes, Node Delta client, and `worker.ts` scheduler were replaced by a Dockerized Python FastAPI backend. The current `Delta-exchange` service owns:

- Supabase bearer-token verification.
- Delta request signing.
- Strategy validation and live strike resolution.
- Entry and exit execution.
- Scheduler polling.
- Position/order/account APIs.
- Fill and execution persistence.

The scheduler runs in the backend lifecycle; there is no current `npm run worker` requirement. Only one scheduler replica should run unless a database lease/leader-election design is added.

### 3.4 Flexible local operation

The project gained flexible local ports:

- Next.js can run on localhost ports such as 3000, 3001, or 3002.
- The Delta backend can use 8000, 8585, 8085, or 8011.
- The frontend probes the configured Delta backend ports.
- `scripts/start-backend.ps1` selects an available port, recreates stale containers, waits for a successful health/scheduler cycle, and prints the selected URL.
- Backend CORS permits local frontend origins on varying ports.

OAuth callback URLs are not automatically flexible; each callback port must be allowlisted in Supabase.

## 4. Trading and strategy work

### 4.1 Strategy builder and execution

The builder supports reusable multi-leg strategies with:

- Buy/sell call and put legs.
- ATM, ITM, OTM, and exact strike selection.
- Lots and per-leg quantities.
- Market and limit entry types.
- Entry and exit times.
- Intraday, BTST, and positional labels.
- Leg-wise or combined-premium risk modes.
- JSON import/export and browser recovery.

Creating or editing a leg does not itself place a Delta order. Execution happens through the explicit execution/scheduling path. Delta contracts with different product IDs cannot be submitted atomically as one multi-contract batch, so legs execute sequentially and fail fast. A later-leg failure can leave earlier legs filled and requires attention.

### 4.2 Persistent saved strategy library

The original run-oriented strategy records were extended into a reusable library:

- Duplicate strategy names are allowed.
- `New strategy` creates a separate definition with new leg IDs and refreshed times.
- `Current strategy` switches between saved definitions.
- Edits autosave after a short delay and can be saved explicitly.
- Switching and scheduling flush pending changes.
- Scheduling produces a separate immutable run linked to its reusable definition.
- Deleting a definition does not delete run history.
- Imported JSON stays a local draft until saved or scheduled.

Migration `003_saved_strategy_library.sql` creates the saved library, RLS policies, backfills existing runs, and links future runs through `saved_strategy_id`.

### 4.3 Scheduling defects and corrections

An early diagnosis found that `Execute now` bypassed future `entry_at` values, while the actual scheduler respected them. This caused an order intended for 5:00 PM to execute around 4:51 PM. The later scheduler/exit implementation separated the paths and ensured future scheduled strategies remain scheduled.

The corrected flow now:

- Keeps future runs in `scheduled` state.
- Prevents cancellation from racing with entry.
- Finds due strategies every two seconds.
- Uses a late-entry safety cutoff (default 60 seconds).
- Attempts overdue exits to reduce risk.
- Cancels still-open recorded entry orders before exit.
- Sends integer-sized, reduce-only market exits.
- Confirms the live per-product position before reporting completion.
- Leaves failed or unconfirmed exits in `attention` for retry.
- Provides `Exit strategy` in run history and `Close` for a dashboard product position.

Live verification showed entries and exits could fill and positions could be confirmed flat. It also showed a scheduled exit occurred roughly 9.5 hours late when the local computer/backend/network was unavailable. The scheduler caught up once connectivity returned. Exact-time unattended operation therefore requires an always-on VPS, stable internet, synchronized clock, healthy Supabase/Delta access, and monitoring.

### 4.4 Bracket-order rejection diagnosis

The HTTP 400 error `bracket order position exists` was traced to Delta allowing only one bracket order for an existing position in the same contract. It was not a malformed application URL. Because strategy legs execute sequentially, users must inspect run history before retrying: an earlier leg can already be filled when a later leg is rejected.

### 4.5 Combined-premium stop loss

The initial application applied stop loss per leg. A value such as `100` was an absolute premium increment, not a 100% combined-strategy stop. The displayed overall stop was initially persisted but not monitored.

The project then implemented a real combined-premium system for the default short ATM call + short ATM put:

- Default combined stop: 100% of actual entry credit.
- A 100% combined stop means the current close cost reaches 2× the net entry credit.
- Actual exchange fills determine entry credit.
- Fresh Delta mark prices determine current close cost.
- Protection arms only after every required entry leg is fully filled.
- State is persisted and the trigger is latched in the database.
- Both legs receive reduce-only closing orders.
- Exit quantity is capped to strategy-owned filled quantity.
- Optional wide per-leg emergency stops can remain exchange-hosted.
- UI states cover awaiting fills, armed, triggered, exiting, and attention.

The implementation requires `002_combined_premium_risk.sql`. Its detailed engineering log is in `COMBINED_PREMIUM_STOP_LOSS.md`.

Important limitation: the combined stop is application-hosted and polling-based. It does not protect the position while the backend or network is unavailable, and a fast move can cross the threshold before closing fills. Exchange-hosted emergency per-leg stops protect different conditions and can trigger before the combined basket threshold.

### 4.6 Current trading risks still documented

Past audits identified risks that should remain visible even where later work reduced them:

- Different option contracts are not atomic; partial multi-leg entry remains possible.
- Strategy ownership must be derived from confirmed fills, not requested quantity or a net account position.
- Exit submission is not equivalent to a confirmed flat position; live reconciliation is required.
- Partial-entry compensation/neutralization needs explicit policy.
- Automated re-entry and cross-leg break-even require safe strategy-level position attribution.
- Unattended live trading needs slippage limits, stale-price rejection, exchange-health gates, global kill switch, margin caps, monitoring, and alerting.

## 5. Market-analysis system

### 5.1 Initial chart

An isolated `Binace` service was created alongside `Delta-exchange`, and a TradingView-style candlestick chart was added to the frontend. The first version used Binance COIN-M `BTCUSD_PERP` REST data and refreshed periodically. This was later superseded.

### 5.2 Current real-time market service

The market service evolved to public Binance Spot `BTCUSDT` REST/WebSocket data. Binance is read-only and never executes orders. Delta remains the execution venue.

The Binance service currently consumes or maintains:

- Executed trades and aggressor side.
- Best bid/ask.
- 100 ms depth updates and a synchronized local order book.
- 24-hour ticker statistics.
- Live candles for 1m, 5m, 15m, 1h, 4h, and 1d.
- Historical candles and recovery snapshots through REST.

It calculates:

- ATR.
- Historical volatility.
- Rolling VWAP.
- 15-minute CVD.
- Spread and order-book imbalance.
- Supply and demand levels.
- Bullish, bearish, or ranging structure.
- Sideways-market probability.

Public Delta `BTCUSD` derivatives context is refreshed separately and includes open interest, OI history, mark/index prices, funding, turnover, quotes, order book, trades, and basis. The UI labels Binance Spot and Delta perpetual data separately because they are different instruments and venues.

Panels below the chart show cumulative depth, a top-10 order-book ladder, spread/liquidity/imbalance, recent aggressive trades, buy/sell flow, Delta open interest, OI history, mark/index prices, funding, turnover, quotes, and Spot-to-mark basis.

### 5.3 Price-difference explanation

The project investigated why the website, TradingView, and Delta showed different BTC prices. The values came from different venues and instruments: Binance derivatives/spot, Bitstamp spot on TradingView, and Delta perpetual. Even Delta exposes separate last-traded, mark, and index prices. Small differences are expected due to order books, liquidity, funding/basis, spreads, and update timing.

### 5.4 Frozen frontend diagnosis and fix

Docker continued receiving Binance and Delta data while the browser appeared frozen. The root cause was the browser WebSocket handshake returning HTTP 431 (`Request Header Fields Too Large`). The initial REST snapshot worked, but continuous updates did not; there was no polling fallback.

The local fix combined:

- `NEXT_PUBLIC_BINANCE_API_URL=http://127.0.0.1:8001`, while continuing to open the frontend through `localhost`.
- A larger WebSocket header limit (`WEBSOCKETS_MAX_LINE_LENGTH`, default 32768).

This prevents localhost authentication cookies from being sent to the market backend and adds a reasonable handshake margin. The solution works for varying localhost frontend ports on the same computer. It does not make `127.0.0.1` reachable from another device.

### 5.5 Chart timeframe race fix

Timeframe switching could produce duplicate candle keys and mixed/out-of-order data. The underlying race allowed an older REST response to overwrite a newer interval while a live candle merged into the wrong dataset. The fix:

- Prevents stale requests from winning.
- Keeps WebSocket candles out until the requested interval is loaded.
- Validates the response interval.
- Deduplicates and sorts by `openTime`.
- Resets hover state and displays loading during switches.

## 6. Market-intelligence research direction

Two analysis tasks established the intended long-term intelligence architecture. The major conclusion was that an LLM should not directly predict and place trades. The system should combine:

- Binance high-frequency spot/microstructure features.
- Delta execution-venue derivatives data, option IV/Greeks/liquidity, funding, OI, positions, and fills.
- Macro/cross-asset context.
- Point-in-time news and scheduled event data.
- Quantitative regime models.
- A policy layer with deterministic risk gates and human approval.

“Sideways” and “volatile” are separate dimensions. A market can be volatile inside a range or trend smoothly. Proposed forecasts include realized volatility, directional efficiency, range/trend probabilities, jump risk, market quality, and a short validity period.

News-source research recommended primary and licensed sources instead of one generic feed: official Federal Reserve/BLS/BEA/Treasury/White House sources, economic-calendar data, fast financial news with corrections, monitored public social accounts, and broad historical discovery sources. Backtests must use received timestamps and unrevised point-in-time data.

The strategy-selection principle is to compare expected movement with Delta-implied movement and execution costs. High expected volatility does not automatically justify buying a straddle if implied volatility is already more expensive. Defined-risk structures and `no trade` were recommended before autonomous naked short-volatility strategies.

The detailed news-system design was added as `docs/13-news-intelligence.md`.

## 7. News intelligence agent

### 7.1 Isolated Agno prototype

An isolated `backend/news_agent` package was first created without touching Delta or Binance execution code. It used Agno with OpenRouter and provided custom web/news tools for:

- Web and news search.
- Reading full articles with BeautifulSoup and JSON-LD.
- Building multi-source dossiers.
- Extracting article-image URLs, captions, dimensions, and provenance.
- Image search metadata.
- Source classification.
- SSRF protection and untrusted-content handling.

The initial model was `poolside/laguna-xs-2.1:free`, and the first output contract was structured/Pydantic. The model was text-only, so image URLs and metadata were collected without claiming pixel-level visual analysis.

### 7.2 Docker, frontend, and Supabase integration

A separate private `news-analyzer` container was added. The authenticated Delta API acts as the gateway; the analyzer is not exposed directly to the browser. A News Intelligence frontend section renders the agent outcome.

Agno persistence moved to Supabase PostgreSQL. The integration added:

- `PostgresDb` session storage.
- Database preflight and meaningful `503 news_database_unavailable` errors.
- Health reporting that includes database readiness.
- A Docker health check requiring database readiness.
- Authenticated, user-scoped session access.

One live verification confirmed a completed session persisted and reloaded. An early model run saved correctly but returned no events/sources, showing that persistence and research quality were separate problems.

### 7.3 Timeout, observability, and research reliability

The browser originally inherited an 8-second request timeout. The analyzer continued for roughly 16–17 seconds and returned HTTP 200 after the browser had already aborted, which produced a misleading `signal timed out` message while a saved outcome still appeared.

The request path was corrected so news analysis no longer inherits that short timeout. Detailed correlated logging was added for request lifecycle, model/session configuration, research tools, article fetching, database operations, durations, validation failures, and exception traces. A production pipeline setting that disabled Agno debug output was also corrected.

The model sometimes skipped tool calls or emitted incomplete “I will search” output. Research collection was made deterministic: live search and article dossiers are collected before synthesis, so the model cannot silently skip evidence gathering.

### 7.4 Current output and model direction

The agent was later simplified from forced structured output to native Markdown. The frontend renders headings, lists, tables, code blocks, and clickable sources. Streaming remains disabled by request.

The most recent task history states that the agent was switched to `deepseek/deepseek-v4-flash-0731`, uses `xhigh` reasoning, and keeps 15 prior runs of history. Application-level output/tool-call/time limits were removed where requested, while security restrictions such as public HTTP/HTTPS-only fetching and private-network blocking remain. Provider context, completion, rate, account-credit, and infrastructure limits still apply externally.

A recorded live run returned HTTP 200 after roughly 199 seconds, produced 9,587 Markdown characters, persisted to Supabase, and reloaded successfully. That is historical verification from the task; it was not re-run during this documentation pass.

### 7.5 Past-session UI

The frontend originally loaded one hard-coded session ID, while Supabase contained multiple session rows. The completed session-browser work added:

- An authenticated user-scoped session-list endpoint.
- Horizontally scrollable session chips with date/time and run count.
- Click-to-load full Markdown session output.
- Automatic loading of the newest saved session.
- Unique IDs for new analyses so they become separate sessions.
- Isolation preventing one user from viewing another user's sessions.

## 8. UI and UX work

The interface went through several deliberate redesigns:

- Larger type scale for body text, controls, labels, headings, tables, metrics, and helper copy.
- Responsive verification at mobile, laptop, and desktop sizes.
- Strategy-page cleanup of misaligned segmented controls and excessive decorative UI.
- Compact risk-control layout.
- Validation that visibly highlights missing/invalid fields, opens the invalid leg, scrolls it into view, and focuses the first invalid control.
- Replacement of “Preview strategy” wording with live execution wording in the relevant flow.
- Removal of the old typed-execution confirmation and preview drawer at the user's request.
- Site rename to `Trade Cognition`.
- `BTC market` renamed to `Market analysis`.
- Black liquid-glass workbench visual system replacing the earlier green theme.
- Continuous surfaces, restrained texture/depth, accessible focus states, hover/press feedback, GSAP motion, and reduced-motion fallbacks.
- Responsive market and authentication views.

One Next.js `OuterLayoutRouter` runtime failure was traced to inconsistent generated `.next` route data rather than application source. Rebuilding `.next` from a clean state fixed `/auth/callback`; the stale cache was backed up outside the repository.

## 9. Docker, networking, and API-key diagnoses

### 9.1 Backend startup

A Docker startup problem had three causes: stale container DNS state, port 8000 already being occupied, and a health check that did not fail when scheduler polling failed. The startup script and Compose health logic were improved. A verified fallback run used port 8585 and confirmed successful Supabase scheduler polling.

### 9.2 Dynamic outbound IP and Delta whitelist

Two separate investigations showed the local public IP had changed over time. Delta correctly rejected requests when the API-key whitelist held the previous address. No source-code fix was required; the current backend egress IP had to be allowlisted.

This is an operational constraint, not an application bug. A residential/dynamic IP may change after reconnects. Reliable live automation should use a server with a static outbound IP and whitelist that address. The IP Delta sees is the backend's egress IP, not the Vercel frontend IP.

### 9.3 Environment-file hygiene

The Git ignore rules were updated to ignore environment example files in the root, backend, and Binance backend during one task. Current repository policy should be reviewed carefully: normally `.env` files containing secrets must be ignored, while sanitized `.env.example` files are useful documentation and are often intentionally tracked. Regardless of ignore policy, real secrets must never be committed.

## 10. Delta documentation created

A modular 14-file Delta documentation set was created under `docs/`:

- Concepts and environments.
- Authentication and signing.
- Reliability and errors.
- Products and instruments.
- REST API.
- Trading and risk.
- WebSockets.
- Python client.
- CCXT integration.
- Recipes.
- Project integration audit.
- Change-watch guidance.
- Questions and answers.
- Documentation index.

The documentation reconciled the local Delta API dump, official Delta documentation, the Python client, and CCXT. It also recorded the distinction between Delta combo/move products and the application's custom multi-leg strategy, and documented why a true combined call+put stop must be monitored by the application.

## 11. Database migrations and persistent state

The project history produced these main migrations:

- `001_initial_schema.sql`: users/profiles, Delta connections, strategies, executions, execution orders, RLS, Vault functions, indexes, triggers, grants, and server-only credential access.
- `002_combined_premium_risk.sql`: combined-risk state, fill reconciliation, thresholds, trigger timestamps, and monitor fields.
- `003_saved_strategy_library.sql`: reusable saved strategies, duplicate names, ownership policies, backfill, and links from immutable runs.

Agno manages its own news-agent session table through PostgreSQL. The recorded default is `ai.news_agent_sessions`.

## 12. Verification history

Across the prior tasks, the following checks were repeatedly reported as passing after the relevant changes:

- TypeScript type checking.
- ESLint.
- Next.js production builds.
- Python Ruff.
- Backend unit tests, with counts increasing as features were added.
- Binance backend tests.
- News-agent/analyzer tests.
- Docker image builds and health checks.
- Desktop and mobile visual QA.
- Live public Binance and Delta market-data checks.
- Controlled live Delta entry/exit checks in the scheduling investigation.
- Live Agno/OpenRouter analysis and Supabase session reload.

These are historical results from their respective tasks. The working tree currently contains uncommitted news-agent and frontend changes, so this documentation pass does not represent a fresh full-suite validation of all application code.

## 13. Task-by-task ledger

The task histories, from the initial build through the most recent work, are summarized below. Some tasks contained multiple follow-up turns.

1. **Build Delta strategy trading UI** — Built the original application; added Supabase auth/Vault/persistence; migrated Node execution and worker logic to Dockerized FastAPI; added email/password auth; documented Google OAuth; introduced backend-optional design mode and flexible ports.
2. **Find Google OAuth form values** — Provided the Supabase callback/origin configuration and explained Google test users.
3. **Docker backend startup issue** — Fixed stale DNS/port collision/weak health checks and added the resilient startup launcher.
4. **Fix API key IP whitelist** — Diagnosed the first dynamic-IP mismatch; no code change.
5. **Verify leg builder trade execution** — Explained exactly which UI actions do and do not place live trades.
6. **Troubleshoot strategy execution 400** — Identified Delta's existing-bracket rejection and partial sequential-execution risk.
7. **Fix scheduler order timing** — Diagnosed `Execute now` bypassing future schedule time; no code change in that diagnosis task.
8. **Enlarge UI typography** — Increased the full responsive type/control scale.
9. **Create modular Delta docs** — Added the modular API/integration documentation set and audited stop-loss semantics.
10. **Analyze combined straddle stop loss** — Designed and implemented the combined-premium risk monitor, migration, UI, tests, and focused progress log.
11. **Analyze BTCUSD market intelligence** — Produced the first deep design report for market regimes, news, forecasting, and guarded strategy selection.
12. **Verify API key IP whitelist** — Diagnosed a later public-IP change and reiterated the static-IP production requirement.
13. **Redesign site UI and UX** — Fixed strategy-page controls/validation/execution wording and repaired stale Next.js route cache.
14. **Fix order scheduling and exits** — Implemented safer scheduling, cancellation, reduce-only exits, position confirmation, retryable attention states, and live verification.
15. **Redesign Trade Cognition UI** — Renamed and rebuilt the product into the black liquid-glass Trade Cognition workbench.
16. **Allow duplicate strategy names** — Added task-like new strategies, then the complete persistent saved-strategy library and migration.
17. **Add BTCUSD TradingView chart** — Added the second market-data container, interactive chart, real-time Binance Spot analysis, Delta derivative panels, and source separation.
18. **List Delta Exchange data** — Cataloged Delta's available public/private data and evaluated Delta-only versus Delta+Binance analysis.
19. **Ignore environment example files** — Updated Git ignore patterns for environment example files.
20. **Improve market analysis text** — Increased Market Analysis title/body prominence without altering behavior.
21. **Analyze BTCUSD market intelligence (2)** — Extended the architecture research, wrote the news specification, created the isolated Agno prototype, integrated the news container/frontend/Supabase, and iterated on the model/database setup.
22. **Fix news analysis agent** — Added the Run Analysis flow, corrected Agno PostgreSQL persistence and health semantics, and verified Supabase saving after credentials were corrected.
23. **Diagnose frozen market data** — Traced the frozen UI to an HTTP 431 WebSocket handshake and implemented the localhost/127.0.0.1 plus header-limit fixes.
24. **Fix Bitcoin chart timeframe issues** — Removed stale-response/live-candle races and duplicate candle timestamps.
25. **Find news agent details** — Audited capabilities, removed forced output limits/structure, improved deterministic research/logging, switched the model per request, and added user-scoped past-session browsing.

## 14. Superseded decisions and wording

Use this section when older chats appear to contradict current code:

- **Node `npm run worker`** → superseded by the Python scheduler inside `Delta-exchange`.
- **COIN-M `BTCUSD_PERP` REST chart** → superseded by live Binance Spot `BTCUSDT` WebSockets, with Delta `BTCUSD` derivatives context.
- **Only per-leg stop loss** → superseded by an optional combined-premium monitor plus optional emergency per-leg protection.
- **Structured Pydantic news output** → superseded by native Markdown output.
- **Local JSON news sessions** → superseded by Agno PostgreSQL sessions in Supabase.
- **One hard-coded news session** → superseded by unique sessions and a user-scoped chip browser.
- **Typed `EXECUTE` confirmation** → removed in later UX work.
- **Testnet/Production selector** → removed from the client-facing UI; production is configured server-side.
- **Site name Delta Strategy Desk** → current product branding is Trade Cognition, although older filenames and documentation may retain the original name.

## 15. Remaining work and operational checklist

The following items remain important before dependable production use:

- Rotate every credential pasted into any prior chat, including Delta, Supabase database/service-role, and OpenRouter credentials.
- Confirm migrations 001, 002, and 003 are applied to the intended Supabase project.
- Deploy all backend services to an always-on host with HTTPS and stable outbound IP.
- Add monitoring/alerts for scheduler health, stale market data, failed exits, attention runs, Supabase errors, and Delta authentication failures.
- Test the complete combined-stop lifecycle with controlled minimal exposure before relying on it.
- Define and test partial-leg failure compensation.
- Verify fill-based ownership and reconciliation for every exit/retry path.
- Add explicit global kill switch, margin/exposure caps, slippage controls, and stale-price/exchange-health gates.
- Keep LLM/news output advisory. Do not give it Delta credentials or direct order tools.
- Evaluate market/news forecasts with point-in-time walk-forward tests before using them for automated strategy selection.
- Decide whether streaming news output is desired; it is currently intentionally disabled.
- Keep the News Analyzer's long runtime visible in the UI and operational logs; live research can take minutes.
- Review `.gitignore` treatment of sanitized `.env.example` files so setup templates remain maintainable without risking real secrets.

## 16. Main reference files

- `README.md` — current setup, architecture, endpoints, scheduling, and deployment.
- `docs/README.md` — modular Delta documentation index.
- `docs/11-project-integration.md` — Delta integration audit.
- `docs/13-news-intelligence.md` — detailed news intelligence design.
- `progress/COMBINED_PREMIUM_STOP_LOSS.md` — combined-risk implementation log.
- `supabase/migrations/001_initial_schema.sql` — initial application persistence/security schema.
- `supabase/migrations/002_combined_premium_risk.sql` — combined-risk persistence.
- `supabase/migrations/003_saved_strategy_library.sql` — reusable strategy library.
- `backend/app/engine.py` — current execution, scheduling, and exit engine.
- `binance_backend/app/feed.py` — current market stream and fan-out service.
- `backend/news_agent/` — news research agent and tools.
- `backend/news_analyzer/main.py` — private analyzer API and session endpoints.
- `app/page.tsx` — main authenticated workspace and strategy flows.
- `app/components/BtcMarketChart.tsx` — market analysis UI.
- `app/components/NewsAnalysis.tsx` — news run/session UI.
