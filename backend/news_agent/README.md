# News research agent

## Reliability changes, 23 October 2026

The curator now attempts 29 baseline sources per asset, plus one bounded topic-specific discovery query
when topics are supplied. Added feeds cover Bank of England news and Coinbase, Kraken and Circle status.
Status feed entries can describe planned maintenance, ongoing incidents or resolved incidents. Their dates
and original wording must be checked before describing a disruption as current.

Both public-source discovery and direct model searches use the same search instance and cache. The final
45 seconds of the default research budget are reserved for article verification and synthesis rather than
new discovery. Curator responses expose remaining calls and time. Article reads and dossier calls retain
their own bounded allowances.

`read_news_article(url, find="phrase")` can return a later passage from retained HTML text without another
download. Up to 128,000 characters are retained per HTML document, while each result remains bounded to
8,000 characters. PDFs retain their existing twelve-page and 8,000-character limit. Missing phrases do not
establish that a statement is absent from the complete source.

Reports omit uncited quantified claims containing currency amounts, percentages or magnitude words.
This is a limited provenance check: cited claims still require passage-level semantic verification, and
uncited non-numerical claims are not automatically proven. Coverage metadata and a report notice identify
partial source collection separately from successful article verification. The Markdown report contract
and existing `verified_urls` metadata remain available.

Main automation captures market and option evidence after news research, then generates charts from that
capture. Collection failures propagate rather than silently reusing the pre-research market snapshot.

Persistent events, structured claim verification, clustering, background collection, social collector
integration and the remaining coverage expansion are subsequent implementation stages. No database
migrations or new analysis triggers are introduced by this stage.

The BTC and ETH automation runs each call `run_news_pipeline` for a news report. The news agent has no order or account tools. It chooses its own searches and article reads, then writes a Markdown report for the automation agent to consider.

Both assets run in the shared `news-analyzer` service, whose container is named `bitcoin-agent`. News research, strategy decisions, and activation rechecks use Agno `debug_mode=True`. Compose also sets `NEWS_LOG_LEVEL=DEBUG`. View detailed runs with `docker logs --timestamps --since 1h -f bitcoin-agent`; the analysis start message identifies the asset and run ID. `Binace` and `Binace-ETH` are market-data services and do not execute AI agents.

## Research flow

1. `curate_public_sources` attempts 29 baseline sources per asset plus a bounded topic query when requested. Official coverage includes Fed, SEC, ECB, BEA, Treasury, CFTC, and White House actions and statements. Publisher and organization coverage includes CoinDesk, Decrypt, Blockworks, The Block, Cointelegraph, Kraken, Delta, Binance, and the relevant Bitcoin Core or Ethereum Foundation source. BLS and Forex Factory provide calendars; Google News and GDELT provide discovery; Polymarket provides expectations. Each source has an explicit available, stale, undated, empty, blocked, timeout, or no-current-items status. Missing sources do not stop research. Blocked or stale sources with configured public discovery fallbacks retain their original status.
2. `search_news` and `web_search` let the agent form follow-up queries. News search asks DDGS for the past week and includes recent Google News RSS discovery leads when DDGS returns too little recent material. Google News links are leads; the agent must find and read the publisher article before citing it. `search_public_discussion` makes site-restricted searches across Reddit, X, Threads, Facebook, and Binance Square. `search_exchange_announcements` finds public notices from Binance, Coinbase, Kraken, OKX, Bybit, and Delta Exchange when announcement listings are blocked. Both tools label search snippets as leads for further reading.
3. `read_news_article` and `build_news_dossier` fetch selected original pages and bounded PDFs, extract readable text and metadata, and retain requested, redirected, and canonical URLs. Access-challenge pages are rejected. Cached reads do not spend another fresh-request allowance. Failed pages return individual errors so the agent can look elsewhere.
4. Calendar selection ranks USD and high-impact releases before limiting the output to sixteen events per calendar. Calendar dates describe scheduled releases, not confirmation of actual publication. Shared snapshots give concurrent BTC and ETH workers the same recently fetched official documents and calendar payloads.
5. The pipeline removes planning text before the first heading and rejects unfinished tool markup. Report blocks that cite unsupported URLs are omitted with an evidence-gap notice. Eligible citations match successful original article/PDF reads or retrieved calendar entries, using full tool results rather than truncated traces. Discovery snippets alone cannot support a report claim. This verifies retrieval provenance, not the truth of every sentence or predictive accuracy.

Research has a four-minute tool budget per run. The production run allows up to 20 web searches and 16 article reads; other tools retain a ten-call allowance. Article downloads, redirects, and body size are also bounded. These are resource limits, not rules about whether the agent may analyze the evidence it found. The source allowlist is empty by default, so the fetcher accepts public HTTP(S) sites while blocking local, private, and reserved targets.

The automation report stores the news Markdown, tool names, `researchTrace`, source health, and `reportQuality` in `ai.analysis_reports.member_responses`. News evidence commits before later market or strategy work can fail. A failed news stage also saves its trace and bounded provider failure details. Credit-limit errors survive the worker-process boundary and have a clear run-history message. Recovery with an empty member response does not erase previously saved news.

Compose mounts a private `news-state` volume at `NEWS_STATE_DIR`. Public source snapshots use OS file locks across spawned workers. Successful snapshots last sixty seconds; failures back off, with blocked endpoints cached for fifteen minutes and HTTP 429 retry delays honoured. Cached material retains its actual check time and last successful fetch time. Application logs use separate bounded files per process and are retained for thirty days across container replacement. Agno debug console logs remain available through Docker.

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

Public feeds and pages change. Binance's listing can return an empty response, CFTC may block retrieval, GDELT and Polymarket may time out, and Forex Factory may rate-limit requests. Blockworks can return a feed frozen in January even though article links parse correctly. These conditions remain visible, with discovery fallbacks where available. Public site searches can retry without a date filter when indexed originals are excluded; undated results remain leads requiring original reads. Social platforms may limit anonymous access. The independent `social_monitor` is not connected to this package, and public discovery does not provide complete platform coverage. Licensed news streams require their own configured provider and access.

Do not add undocumented exchange endpoints or bypass site access restrictions. Check a source's current terms and published interface before changing its connector.
