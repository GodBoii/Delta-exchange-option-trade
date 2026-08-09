# Agno news intelligence prototype

This is an isolated, read-only prototype. It is not imported by the Delta FastAPI application or the Binance collector and has no trading tools.

The runtime uses two Agno agents because the small free model is more reliable when research tool selection and the large structured report schema are handled in separate turns. The source researcher searches, opens pages, and collects image provenance. The analyst converts that dossier into `NewsAnalysisReport`.

## Capabilities

- General web and news search through Agno `WebSearchTools` and DDGS.
- Bounded BeautifulSoup article extraction with title, publisher, dates, canonical URL, readable text, and image metadata.
- Multi-article evidence dossiers for corroboration and contradiction checks.
- News image search and article image extraction with source-page provenance.
- Transparent domain classification for official and established sources.
- Pydantic-validated `NewsAnalysisReport` output when the model follows the prompted schema.
- Persistent Agno sessions through `JsonDb`, with the latest configured runs included as conversational context.
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

Without `--session-id`, the CLI uses `news-research-default`. The structured analyst uses that session ID and the source researcher uses the related `<session-id>:research` thread. Session messages, responses, run metadata, and tool calls are written automatically to `news_agent/data/json_db/news_agent_sessions.json`. JSON output includes both session IDs and the run ID.

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
NEWS_AGENT_APP_NAME=Delta News Intelligence Prototype
NEWS_AGENT_HTTP_REFERER=
NEWS_AGENT_SEARCH_BACKEND=auto
NEWS_AGENT_SEARCH_REGION=wt-wt
NEWS_AGENT_SEARCH_TIMEOUT_SECONDS=12
NEWS_AGENT_ARTICLE_TIMEOUT_SECONDS=15
NEWS_AGENT_MAX_ARTICLE_CHARS=20000
NEWS_AGENT_MAX_DOWNLOAD_BYTES=2000000
NEWS_AGENT_MAX_REDIRECTS=3
NEWS_AGENT_ALLOWED_DOMAINS=
NEWS_AGENT_SESSION_DB_PATH=news_agent/data/json_db
NEWS_AGENT_SESSION_TABLE=news_agent_sessions
NEWS_AGENT_DEFAULT_SESSION_ID=news-research-default
NEWS_AGENT_DEFAULT_USER_ID=local-user
NEWS_AGENT_HISTORY_RUNS=3
```

When `NEWS_AGENT_ALLOWED_DOMAINS` is non-empty, the article reader only fetches those comma-separated domains and their subdomains. Leaving it empty permits public HTTP(S) domains while still blocking non-public network targets.

## Important limitations

Search results and scraped pages can be incomplete, stale, copyrighted, adversarial, or incorrect. Website terms and robots policies remain applicable. The URL validator reduces SSRF risk but does not turn this prototype into a hardened public scraping proxy. Use an explicit domain allowlist before exposing it as a public service.

OpenRouter model availability, provider routing, pricing, and rate limits can change. The configured free fallback currently advertises tool calling but not native response formatting, so Agno places the Pydantic schema in the prompt and validates the returned JSON locally. The CLI warns if the returned content does not validate. OpenRouter may use free-model inputs and outputs for model improvement; do not send secrets or private trading data through this prototype.

Agno documents `JsonDb` as a lightweight option for demos and testing, not a production database. It rewrites local JSON files and does not provide the concurrency and transactional guarantees needed by a multi-worker service. Move sessions to SQLite or PostgreSQL before integrating this prototype into the deployed backend.
