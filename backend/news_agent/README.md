# Agno news intelligence prototype

This is an isolated, read-only prototype. It is not imported by the Delta FastAPI application or the Binance collector and has no trading tools.

The runtime uses one Agno agent. Agno handles tool calls, structured output, session persistence, and PostgreSQL table creation.

## Capabilities

- General web and news search through Agno `WebSearchTools` and DDGS.
- Bounded BeautifulSoup article extraction with title, publisher, dates, canonical URL, readable text, and image metadata.
- Multi-article evidence dossiers for corroboration and contradiction checks.
- News image search and article image extraction with source-page provenance.
- Transparent domain classification for official and established sources.
- Pydantic-validated `NewsAnalysisReport` output when the model follows the prompted schema.
- Persistent Agno sessions in Supabase PostgreSQL through `PostgresDb`.
- SSRF protections that block localhost, private/reserved addresses, credentials, and nonstandard ports.

The configured `poolside/laguna-xs-2.1:free` fallback model is text-only. This prototype returns images as URLs and metadata for display but does not send image pixels to the model, so the agent is prohibited from claiming visual analysis.

## Install

From the `backend` directory:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-news-agent.txt
```

Add the OpenRouter key to `backend/.env`:

```text
OPENROUTER_API_KEY=sk-or-v1-your-key
```

## Run

```powershell
.venv\Scripts\python.exe -m news_agent "Analyze today's highest-impact Bitcoin news and explain the volatility channels"
```

JSON output:

```powershell
.venv\Scripts\python.exe -m news_agent --json "Analyze recent US trade-policy news relevant to BTC"
```

Save a Markdown report containing source links and renderable image URLs:

```powershell
.venv\Scripts\python.exe -m news_agent --save reports\btc-news.md "Analyze the latest Bitcoin ETF and regulation news"
```

Continue a named research session across separate CLI runs:

```powershell
.venv\Scripts\python.exe -m news_agent --session-id btc-macro --user-id local-user "Analyze today's BTC macro risks"
.venv\Scripts\python.exe -m news_agent --session-id btc-macro --user-id local-user "What changed since the previous report?"
```

Without `--session-id`, the CLI uses `news-research-default`. Session messages, responses, run metadata, and tool calls are written automatically by Agno. `PostgresDb(db_url=...)` creates its tables on first use. JSON output includes the session ID and run ID.

## Test

```powershell
.venv\Scripts\python.exe -m pytest news_agent\tests
.venv\Scripts\python.exe -m ruff check news_agent
```

Tests do not require an OpenRouter key or live internet access.

## Configuration

`backend/.env` supports:

```text
OPENROUTER_API_KEY=
NEWS_AGENT_MODEL=poolside/laguna-xs-2.1:free
NEWS_AGENT_ALLOWED_DOMAINS=
SUPABASE_DB_URL=postgresql://postgres:password@db.project-ref.supabase.co:5432/postgres
NEWS_AGENT_DEFAULT_SESSION_ID=news-research-default
NEWS_AGENT_DEFAULT_USER_ID=local-user
```

When `NEWS_AGENT_ALLOWED_DOMAINS` is non-empty, the article reader only fetches those comma-separated domains and their subdomains. Leaving it empty permits public HTTP(S) domains while still blocking non-public network targets.

## Important limitations

Search results and scraped pages can be incomplete, stale, copyrighted, adversarial, or incorrect. Website terms and robots policies remain applicable. The URL validator reduces SSRF risk but does not turn this prototype into a hardened public scraping proxy. Use an explicit domain allowlist before exposing it as a public service.

OpenRouter model availability, provider routing, pricing, and rate limits can change. The configured free fallback currently advertises tool calling but not native response formatting, so Agno places the Pydantic schema in the prompt and validates the returned JSON locally. The CLI warns if the returned content does not validate. OpenRouter may use free-model inputs and outputs for model improvement; do not send secrets or private trading data through this prototype.

The production agent uses Agno `PostgresDb` with Supabase. `SUPABASE_DB_URL` is a server-only PostgreSQL connection URI and must never be exposed through a `NEXT_PUBLIC_` variable. The dedicated `news-analyzer` container is the only service that needs this URI and the OpenRouter key.
