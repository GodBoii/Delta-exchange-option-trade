# Agno news intelligence prototype

This is an isolated, read-only prototype. It is not imported by the Delta FastAPI application or the Binance collector and has no trading tools.

The runtime uses deterministic evidence collection followed by one Agno synthesis agent. Agno handles the native Markdown response, session persistence, and PostgreSQL table creation.

## Capabilities

- General web and news search through Agno `WebSearchTools` and DDGS.
- BeautifulSoup article extraction with title, publisher, dates, canonical URL, readable text, and image metadata.
- Multi-article evidence dossiers for corroboration and contradiction checks.
- News image search and article image extraction with source-page provenance.
- Transparent domain classification for official and established sources.
- Native model-authored Markdown output with clickable source links; no JSON or structured-output schema is forced.
- Persistent Agno sessions in Supabase PostgreSQL through `PostgresDb`.
- SSRF protections that block localhost, private/reserved addresses, credentials, and nonstandard ports.

The news agent uses the multimodal `z-ai/glm-5.3-flash` model through OpenRouter with `xhigh` reasoning effort. Agno sends images attached to a run to the model, so the news member can inspect the server-rendered BTC chart images forwarded by the automation team. Article research tools still return publisher image URLs and metadata without downloading arbitrary image files into the standalone news pipeline. The automation team's main agent continues to use `deepseek/deepseek-v4-flash-vision-exp`.

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
SUPABASE_DB_URL=postgresql://postgres.project-ref:password@aws-1-region.pooler.supabase.com:5432/postgres
```

For the shared Supabase Session pooler, the database username must be
`postgres.<project-ref>`. Copy the complete URI from **Supabase Dashboard →
Connect → Session pooler** rather than assembling it by hand, and use the
intended application project. Percent-encode special characters in the database
password. The analyzer adds SSL connection settings
without changing credentials.

Agno v2 stores all runs for a session in one row. This service explicitly uses
`ai.news_agent_sessions`, so the table location is deterministic. The schema
and table are created on the first successful run.

Non-secret operational settings live beside the implementation instead of in
`.env`. The model and session defaults are in `config.py`. The application
does not impose output-token, completion-token, reasoning-step, tool-call,
article-time, article-size, extracted-text, redirect-count, or analysis-concurrency
caps. Up to 15 prior session runs are added to model context. The source
allowlist is empty by default, which permits public HTTP(S) domains while still
blocking localhost and non-public network targets.

## Important limitations

Search results and scraped pages can be incomplete, stale, copyrighted, adversarial, or incorrect. Website terms and robots policies remain applicable. The URL validator reduces SSRF risk but does not turn this prototype into a hardened public scraping proxy. Use an explicit domain allowlist before exposing it as a public service.

OpenRouter model availability, context windows, provider routing, pricing, rate limits, and model-side generation limits can change and are not controlled by this application. The model writes normal Markdown without a forced response schema. Do not send secrets or private trading data through this prototype.

The production agent uses Agno `PostgresDb` with Supabase. `SUPABASE_DB_URL` is a server-only PostgreSQL connection URI and must never be exposed through a `NEXT_PUBLIC_` variable. The dedicated `news-analyzer` container is the only service that needs this URI and the OpenRouter key. `GET /health` reports `databaseReady`, `databaseSchema`, and `sessionTable`; it never returns the connection URI or password.
