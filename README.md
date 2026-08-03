# Delta Strategy Desk

A client-facing Delta Exchange India options strategy workstation with a Next.js frontend, Supabase Auth/Postgres/Vault, and a Dockerized Python FastAPI trading backend.

## Architecture

- **Frontend:** Next.js on Vercel.
- **Authentication:** Supabase email/password and optional Google OAuth.
- **Persistence:** Supabase Postgres with Row Level Security.
- **Delta credentials:** Supabase Vault, accessed only with the server-side service role.
- **Trading API and scheduler:** Python FastAPI in one continuously running Docker container on a static-IP server.

The frontend never receives a Delta secret or Supabase service-role key. It sends the user's Supabase access token to the Python API, which verifies the token with Supabase before accessing any user-scoped data.

The frontend also supports a backend-optional design mode. Supabase sign-in, strategy configuration, automatic browser draft storage, and JSON export/import work without the Python service. Delta connection, live contract preview, scheduling, execution, dashboard data, and run history require the local Docker backend.

## Repository layout

```text
app/                         Next.js client UI and Supabase OAuth callback
lib/                         Browser/server Supabase helpers and frontend types
backend/app/                 FastAPI, Delta client, execution engine, scheduler
backend/tests/               Python strategy safety tests
backend/Dockerfile           Production Python image
docker-compose.yml           Single-replica backend deployment
supabase/migrations/         Database, RLS, and Vault functions
```

## Local frontend

Create `.env.local` from `.env.example` and set:

```text
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=...
NEXT_PUBLIC_SITE_URL=http://localhost:3000
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_API_PORTS=8000,8585,8085,8011,8001
SUPABASE_SERVICE_ROLE_KEY=...
DELTA_PRODUCTION_URL=https://api.india.delta.exchange
```

Then run:

```powershell
npm install
npm run dev
```

## Local Python backend with Docker

The Compose service reads server credentials from the ignored root `.env.local` file.

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f backend
```

The API is available at `http://localhost:8000`, its health endpoint is `/health`, and interactive API documentation is available at `/docs`.

### Flexible local ports

The frontend is not tied to port 3000. Next.js may run on 3000, 3001, 3002, or another local port; the Python backend accepts authenticated browser requests from any `localhost` or `127.0.0.1` port.

For Google OAuth and email-confirmation callbacks, add every local frontend callback you plan to use to Supabase Authentication > URL Configuration, for example `http://localhost:3000/auth/callback`, `http://localhost:3001/auth/callback`, and `http://localhost:3002/auth/callback`.

The frontend probes the backend ports listed in `NEXT_PUBLIC_API_PORTS` in parallel and uses the first endpoint that returns a valid Delta Strategy API health response. An explicit `NEXT_PUBLIC_API_URL` is preferred but is not mandatory locally.

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

The frontend will discover `http://localhost:8585` automatically. Other included fallback ports are 8085, 8011, and 8001. To use another port, add it to `NEXT_PUBLIC_API_PORTS`, restart the frontend, and set the same `BACKEND_PORT` before starting Compose.

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

Deploy the Docker service to an always-on VPS or container host with a stable outbound public IP. On the server, create an ignored `.env.local` containing the server variables, then run:

```bash
docker compose up -d --build
```

Expose the API through HTTPS using a reverse proxy such as Caddy or Nginx. The Vercel frontend must use an `https://` API URL; browsers will block an HTTP backend from an HTTPS page.

Required backend variables are documented in `backend/.env.example`. `FRONTEND_ORIGINS` already includes:

```text
https://delta-exchange-option-trade.vercel.app
```

Run exactly one backend container and one Uvicorn worker. Multiple scheduler replicas require a separate database lease design.

Add the backend server's static public IP to the Delta API key allowlist. Vercel's IP is not used for Delta requests.

## Scheduling behavior

1. The authenticated user previews and schedules a strategy.
2. The strategy is stored in Supabase.
3. The Python scheduler finds due entries every two seconds.
4. It resolves current option contracts and submits legs sequentially to Delta.
5. At the configured exit time, it sends reduce-only market orders for the recorded products.
6. Every execution and order response is recorded in Supabase.

Entries more than `MAX_ENTRY_LATENESS_SECONDS` late are not submitted. They are marked `attention` instead, preventing a restarted server from placing an unexpectedly stale trade. The default is 60 seconds. Late exits are still attempted to reduce open risk.

Delta cannot atomically submit option legs with different product IDs. A later leg can fail after earlier legs have filled. Overall strategy target/stop, cross-leg break-even, and automatic re-entry remain previewed and persisted but are not automatically monitored by this version.

## Working away from the backend

On `https://delta-exchange-option-trade.vercel.app` without the Docker service:

- Email/password and configured Google authentication continue to work through Supabase.
- The full leg and strategy builder remains usable.
- Draft changes are saved automatically in that browser's local storage.
- Use **Export** to download a strategy JSON file.

At home, start Docker and the local frontend, sign in, and use **Import** to load that JSON file. You can then connect Delta, preview live contracts, schedule, or execute.

Browser storage is isolated by website origin, so the deployed site and `localhost` cannot directly share local storage. Export/import is the deliberate transfer mechanism.

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
