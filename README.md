# Delta Strategy Desk

A client-facing Delta Exchange India options strategy workstation with a Next.js frontend, Supabase Auth/Postgres/Vault, and a Dockerized Python FastAPI trading backend.

## Architecture

- **Frontend:** Next.js on Vercel.
- **Authentication:** Supabase email/password and optional Google OAuth.
- **Persistence:** Supabase Postgres with Row Level Security.
- **Delta credentials:** Supabase Vault, accessed only with the server-side service role.
- **Trading API and scheduler:** Python FastAPI in the `Delta-exchange` container on a static-IP server.
- **BTC spot analysis:** A separate read-only FastAPI service in the `Binace` container, backed by the public Binance Spot REST and WebSocket APIs. It never places orders.

The frontend never receives a Delta secret or Supabase service-role key. It sends the user's Supabase access token to the Python API, which verifies the token with Supabase before accessing any user-scoped data.

The frontend also supports a backend-optional design mode. Supabase sign-in, the persistent saved-strategy library, strategy configuration, automatic browser recovery storage, and JSON export/import work without the Python service. Delta connection, live contract preview, scheduling, execution, dashboard data, and run history require the local Docker backend.

## Repository layout

```text
app/                         Next.js client UI and Supabase OAuth callback
lib/                         Browser/server Supabase helpers and frontend types
backend/app/                 FastAPI, Delta client, execution engine, scheduler
backend/tests/               Python strategy safety tests
backend/Dockerfile           Production Python image
binance_backend/app/         Binance Spot stream, synchronized book, and analysis API
binance_backend/tests/       Market-data, analysis, and stream-state tests
docker-compose.yml           Two-service backend deployment
supabase/migrations/         Database, RLS, and Vault functions
```

## Local frontend

Create `.env.local` from `.env.example` and set:

```text
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=...
NEXT_PUBLIC_SITE_URL=http://localhost:3000
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_API_PORTS=8000,8585,8085,8011
NEXT_PUBLIC_BINANCE_API_URL=http://localhost:8001
SUPABASE_SERVICE_ROLE_KEY=...
DELTA_PRODUCTION_URL=https://api.india.delta.exchange
```

Then run:

```powershell
npm install
npm run dev
```

### Apply the saved-strategy library migration

If migrations `001` and `002` are already installed in your Supabase project, open **Supabase Dashboard → SQL Editor**, paste the entire contents of:

```text
supabase/migrations/003_saved_strategy_library.sql
```

and choose **Run** once. The migration creates the private `saved_strategies` table, RLS ownership policies, the optional link from execution runs to their saved definition, indexes, and grants. It also backfills every existing strategy run into the saved library without changing or deleting run history. Reusable strategy names are intentionally not unique.

## Local Python backend with Docker

The Compose service reads server credentials from the ignored root `.env.local` file.

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f delta-exchange binace
```

The Delta trading API is available at `http://localhost:8000`; the Binance Spot analysis API is available at `http://localhost:8001`. Both expose `/health` and `/docs`.

Compose creates the requested containers:

- `Delta-exchange`: existing authenticated Delta trading API and scheduler.
- `Binace`: public Binance `BTCUSDT` Spot analysis API. The spelling intentionally matches the requested container name.

The market service exposes:

- `GET /api/market/btcusd`: live Spot ticker, candles, and the current analysis snapshot. The legacy path is retained for frontend compatibility.
- `GET /api/market/btcusd/candles`: up to 1,500 candles per request, with interval/start/end controls.
- `GET /api/market/btcusd/ticker`: normalized 24-hour market statistics.
- `GET /api/market/btcusd/order-book`: synchronized local Spot order book.
- `GET /api/market/btcusd/trades`: recent Spot trades.
- `GET /api/market/btcusd/analysis`: ATR, historical volatility, VWAP, CVD, supply/demand, market structure, order-book imbalance, and sideways probability.
- `WS /ws/market/btcusd`: throttled real-time browser feed containing ticker, bid/ask, active candle, top-15 synchronized depth, recent aggressor-classified trades, analysis updates, and public Delta BTCUSD derivative context.

The chart is available publicly at `http://localhost:3000/market` and inside the connected workspace under **BTC market**. A single server-side Binance connection fans out live updates to browsers; REST is used only for initial candle history and recovery. It supports 1-minute through daily candle views.

No Binance API key is required because this service consumes public market data only. Delta remains the sole venue for credentials, order submission, positions, and execution. `BTCUSDT` is deliberately shown everywhere in the analysis UI so it is not confused with Delta's separate `BTCUSD` contract price.

The `Binace` service subscribes to public trade, best bid/ask, 100 ms diff-depth, 24-hour ticker, and kline streams. It also reads the unauthenticated Delta `BTCUSD` ticker every five seconds for open interest, mark/index prices, funding, turnover, and Delta bid/ask context. Set `BINANCE_BASE_URL`, `BINANCE_WS_URL`, `BINANCE_SYMBOL`, `DELTA_PUBLIC_BASE_URL`, `DELTA_SYMBOL`, `DELTA_CONTEXT_SECONDS`, `MARKET_BROADCAST_SECONDS`, or `CVD_WINDOW_SECONDS` only when overriding their documented defaults in `binance_backend/.env.example`. The Docker health check requires a connected Binance stream, a synchronized order book, and an event newer than 30 seconds.

Below the candlestick chart, the UI keeps the sources visibly separated:

- Binance Spot `BTCUSDT`: depth curve, top-10 bid/ask ladder, spread, liquidity balance, and recent aggressive trade flow.
- Delta perpetual `BTCUSD`: open interest and its session history, mark/index prices, funding rate, turnover, Delta quotes, and Spot-to-mark basis.

### Flexible local ports

The frontend is not tied to port 3000. Next.js may run on 3000, 3001, 3002, or another local port; the Python backend accepts authenticated browser requests from any `localhost` or `127.0.0.1` port.

For Google OAuth and email-confirmation callbacks, add every local frontend callback you plan to use to Supabase Authentication > URL Configuration, for example `http://localhost:3000/auth/callback`, `http://localhost:3001/auth/callback`, and `http://localhost:3002/auth/callback`.

The frontend probes the Delta backend ports listed in `NEXT_PUBLIC_API_PORTS` in parallel and uses the first endpoint that returns a valid Delta Strategy API health response. Port 8001 is reserved for `Binace` and must not be included in that fallback list. An explicit `NEXT_PUBLIC_API_URL` is preferred but is not mandatory locally.

Use the automatic local launcher. It checks the configured backend ports and starts Docker on the first available one:

```powershell
.\scripts\start-backend.ps1
```

If you prefer to choose the backend port yourself:

```powershell
$env:BACKEND_PORT=8585
docker compose up -d --build
npm run dev
```

The frontend will discover `http://localhost:8585` automatically. Other included Delta fallback ports are 8085 and 8011. To use another port, add it to `NEXT_PUBLIC_API_PORTS`, restart the frontend, and set the same `BACKEND_PORT` before starting Compose. Keep it distinct from `BINANCE_PORT`, which defaults to 8001.

The scheduler is part of the Python application lifecycle. There is no separate npm worker command. It checks Supabase every two seconds while the container is healthy.

To stop it:

```powershell
docker compose down
```

## Production deployment

### Vercel frontend

For frontend-only design mode, configure these Vercel environment variables and redeploy:

```text
NEXT_PUBLIC_SUPABASE_URL=https://xphxxkmeqqgjobkmclso.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=your-publishable-key
NEXT_PUBLIC_SITE_URL=https://delta-exchange-option-trade.vercel.app
```

Do not add `SUPABASE_SERVICE_ROLE_KEY` or Delta API secrets to Vercel. You can omit `NEXT_PUBLIC_API_URL` while the backend remains local. The deployed HTTPS site will automatically enter design mode instead of trying to use the HTTP localhost backend.

If you later expose the Docker API through a public HTTPS domain, add:

```text
NEXT_PUBLIC_API_URL=https://your-python-api-domain.example
NEXT_PUBLIC_BINANCE_API_URL=https://your-market-api-domain.example
```

### Supabase Auth URLs

Use this Site URL:

```text
https://delta-exchange-option-trade.vercel.app
```

Add this Redirect URL:

```text
https://delta-exchange-option-trade.vercel.app/auth/callback
```

### Docker backend

Deploy both Docker services to an always-on VPS or container host. The Delta service still needs a stable outbound public IP. On the server, create an ignored `.env.local` containing the server variables, then run:

```bash
docker compose up -d --build
```

Expose the API through HTTPS using a reverse proxy such as Caddy or Nginx. The Vercel frontend must use an `https://` API URL; browsers will block an HTTP backend from an HTTPS page.

Required backend variables are documented in `backend/.env.example`. `FRONTEND_ORIGINS` already includes:

```text
https://delta-exchange-option-trade.vercel.app
```

Run exactly one `Delta-exchange` container and one Uvicorn worker. Multiple scheduler replicas require a separate database lease design. `Binace` is isolated from Supabase and Delta credentials and only accesses public market endpoints.

Add the backend server's static public IP to the Delta API key allowlist. Vercel's IP is not used for Delta requests.

## Scheduling behavior

1. The authenticated user creates or selects a reusable definition from `saved_strategies`.
2. Builder changes are saved to Supabase; the browser also keeps a recovery copy.
3. The user previews and schedules the selected definition.
4. A separate immutable run is inserted into `strategies` and linked through `saved_strategy_id`.
5. The Python scheduler finds due entries every two seconds.
6. It resolves current option contracts and submits legs sequentially to Delta.
7. At the configured exit time, it cancels any still-open recorded entry orders and sends reduce-only market orders for the recorded fills.
8. It checks Delta's real-time per-product position endpoint and marks the strategy complete only after the reduction is confirmed.
9. Every execution and order response is recorded in Supabase. Failed or unconfirmed exits remain `attention` and can be retried from Run history.

The builder's **Schedule strategy** action stores a scheduled run; it does not place orders immediately. Use **Exit strategy** in Run history to close a live or attention-required strategy early. The Dashboard's **Close** action closes the entire live position for one product after first cancelling that product's open orders. Both close actions use reduce-only market orders and verify the live position before reporting success.

Every scheduled strategy is a separate run identified by its generated UUID. Strategy names are labels and may be reused.

## Saved strategy library

- **Current strategy** switches between reusable definitions already saved in Supabase.
- **New strategy** saves a fresh short-straddle definition with new entry/exit times and selects it without modifying the previous strategy.
- Builder edits are saved automatically after a short delay. **Save strategy** is available for an immediate explicit save.
- Switching or scheduling first flushes pending changes, so the selected definition and the scheduled run use the same snapshot.
- **Delete** removes only the reusable definition. The foreign key uses `on delete set null`, so scheduled, active, completed, and attention-required run history remains intact.
- Imported JSON opens as a new local draft and is added to the library only when saved or scheduled.
- When an older saved strategy has expired entry times, selecting it refreshes the schedule while retaining its configured duration.

Entries more than `MAX_ENTRY_LATENESS_SECONDS` late are not submitted. They are marked `attention` instead, preventing a restarted server from placing an unexpectedly stale trade. The default is 60 seconds. Late exits are still attempted to reduce open risk.

Delta cannot atomically submit option legs with different product IDs. A later leg can fail after earlier legs have filled. Overall strategy target/stop, cross-leg break-even, and automatic re-entry remain previewed and persisted but are not automatically monitored by this version.

## Working away from the backend

On `https://delta-exchange-option-trade.vercel.app` without the Docker service:

- Email/password and configured Google authentication continue to work through Supabase.
- Saved strategies can be created, edited, deleted, and switched through Supabase RLS.
- The full leg and strategy builder remains usable.
- The current strategy is also cached automatically in browser storage for recovery.
- Use **Export** to download a strategy JSON file.

At home, start Docker and the local frontend, sign in, and use **Import** to load that JSON file. You can then connect Delta, preview live contracts, schedule, or execute.

The Supabase strategy library follows the signed-in user across origins and devices. Browser recovery storage is still isolated by website origin; export/import remains available for portable files and offline recovery.

## Validation

Frontend:

```powershell
npm run lint
npm run typecheck
npm run build
```

Backend:

```powershell
cd backend
.venv\Scripts\python -m ruff check app tests
.venv\Scripts\python -m pytest
```

Market data backend:

```powershell
cd binance_backend
..\backend\.venv\Scripts\python -m ruff check app tests
..\backend\.venv\Scripts\python -m pytest
```

Docker:

```powershell
docker compose build
docker compose up -d
```

## Security notes

- Never expose `SUPABASE_SERVICE_ROLE_KEY` through a `NEXT_PUBLIC_` variable.
- Never store Delta secrets in frontend code, browser storage, Git, or Docker images.
- Keep `.env.local` only on the trusted backend server.
- Use a dedicated Delta trading key with only required permissions and the backend IP allowlisted.
- Keep the server clock synchronized; Delta rejects stale request signatures.
- Configure TLS, container restart policies, health monitoring, centralized logs, and alerts before unattended live trading.
