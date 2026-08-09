# Agno news intelligence prototype

This is an isolated, read-only prototype. It is not imported by the Delta FastAPI application or the Binance collector and has no trading tools.

## Capabilities

- General web and news search through Agno `WebSearchTools` and DDGS.
- Bounded BeautifulSoup article extraction with title, publisher, dates, canonical URL, readable text, and image metadata.
- Multi-article evidence dossiers for corroboration and contradiction checks.
- News image search and article image extraction with source-page provenance.
- Transparent domain classification for official and established sources.
- Pydantic-validated `NewsAnalysisReport` output when the model follows the prompted schema.
- SSRF protections that block localhost, private/reserved addresses, credentials, and nonstandard ports.

The configured `xiaomi/mimo-v2.5` model supports multimodal input. This prototype currently returns images as URLs and metadata for display but does not download or send image pixels to the model, so the agent is prohibited from claiming visual analysis.

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
NEWS_AGENT_MODEL=xiaomi/mimo-v2.5
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
```

When `NEWS_AGENT_ALLOWED_DOMAINS` is non-empty, the article reader only fetches those comma-separated domains and their subdomains. Leaving it empty permits public HTTP(S) domains while still blocking non-public network targets.

## Important limitations

Search results and scraped pages can be incomplete, stale, copyrighted, adversarial, or incorrect. Website terms and robots policies remain applicable. The URL validator reduces SSRF risk but does not turn this prototype into a hardened public scraping proxy. Use an explicit domain allowlist before exposing it as a public service.

OpenRouter model availability, provider routing, pricing, and rate limits can change. The selected model currently advertises tool calling and native response formatting, so Agno requests the Pydantic output schema from the provider. The CLI warns if the returned content does not validate.
