# News research agent

The BTC and ETH automation runs each call `run_news_pipeline` for a news report. The news agent has no order or account tools. It chooses its own searches and article reads, then writes a Markdown report for the automation agent to consider.

## Research flow

1. `curate_public_sources` reads public official feeds, crypto publisher feeds, exchange and project announcements, economic calendars, GDELT discovery, and Polymarket event pages. It returns dated source cards and an error for each unavailable source. A missing source does not stop research.
2. `search_news` and `web_search` let the agent form follow-up queries. `search_public_discussion` makes site-restricted searches across Reddit, X, Threads, Facebook, and Binance Square. `search_exchange_announcements` finds public notices from Binance, Coinbase, Kraken, OKX, Bybit, and Delta Exchange when announcement listings are blocked. Both tools label search snippets as leads for further reading.
3. `read_news_article` and `build_news_dossier` fetch selected original pages, extract readable text and metadata, and retain source URLs. Failed pages return individual errors so the agent can look elsewhere.
4. The agent separates reported events, scheduled meetings, market expectations, and public discussion. It cites material claims and states what it could not verify.

Research has a four-minute tool budget per run. Each search or article tool has a ten-call allowance; article downloads, redirects, and body size are also bounded. These are resource limits, not rules about whether the agent may analyze the evidence it found. The source allowlist is empty by default, so the fetcher accepts public HTTP(S) sites while blocking local, private, and reserved targets.

The automation report stores the news Markdown, tool names, and `researchTrace` in `ai.analysis_reports.member_responses`. The trace records queries, source URLs, counts, durations, brief excerpts, and failures without copying complete article bodies. Automation and activation behavior is outside this package.

## Run and test

From `backend`:

```powershell
.venv\Scripts\python.exe -m news_agent "Analyze Bitcoin news and upcoming macro meetings"
.venv\Scripts\python.exe -m pytest news_agent\tests
.venv\Scripts\python.exe -m ruff check news_agent
```

The CLI defaults to BTC. Automation passes its BTC or ETH asset profile and corresponding search focus. `--json` includes the report, model, session ID, run ID, and research tool names. `--save` writes the report to a chosen path.

`backend/.env` needs `OPENROUTER_API_KEY` for model calls and `AI_DATABASE_URL` for persistent standalone sessions. Automation uses an in-memory news session per run. The model default and database settings are in `config.py`.

## Source limitations

Public feeds and pages change. Binance's announcement page can return an empty automated response, GDELT may time out, and Forex Factory may rate-limit requests. The agent receives these as source errors and can search for alternate accounts. Social platforms may limit anonymous page access; public web discovery does not provide complete platform coverage. Search snippets, publisher dates, article text, and prediction-market prices have different meanings and should remain labelled in the report.

Do not add undocumented exchange endpoints or bypass site access restrictions. Check a source's current terms and published interface before changing its connector.
