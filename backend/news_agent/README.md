# News research agent

BTC and ETH automation use the same isolated Agno/OpenRouter news implementation. The news agent has
no order, account, balance or execution tools. Main automation collects fresh market and option evidence
after news completes and creates charts from that capture.

## Current research flow

1. `news-collector` polls public sources for both assets independently of model runs. Shared file-cache locks
   reuse common macro/calendar responses across assets and worker processes. Recent discovery snapshots
   are supplied to research immediately, with original dates preserved. They do not trigger analyses.
2. The curator attempts 35 baseline connectors per asset, plus one bounded topic-specific query when
   topics are supplied. Coverage includes official US releases/calendars, ECB, Bank of England, BIS,
   English/Japanese Bank of Japan, crypto publishers, exchange/issuer status, and labelled institutional,
   infrastructure and regional discovery queries. Discovery coverage is not verified original coverage.
3. Binance listings, both GDELT queries and Polymarket use explicitly labelled public discovery replacements
   by default after repeated observed failures. Source-health records retain `replacement_for` and
   `original_status`. `NEWS_TRY_UNRELIABLE_SOURCES=true` enables direct attempts for diagnosis. No private
   or undocumented exchange endpoint or access-control workaround is used. A quiet official feed is not
   proof of a broken connector. RSS 1.0, RSS 2.0 and Atom are parsed. Atom update times are not publication.
4. Public discussion reads sanitized X/Reddit exports where configured, then fills gaps with site searches.
   Samples are labelled as discussion, never population sentiment or proof of an event. Original article
   reads remain mandatory for factual reporting. Search instances share their cache and research budget.
5. Original HTML/PDF reads return text, dates, language, provenance and stable evidence IDs. HTML and the
   first twelve PDF pages retain up to 128,000 characters. Each response is capped at 8,000 characters.
   `read_news_article(url, find="phrase")` or `read_news_article(url, page=2)` retrieves a later retained
   passage without another download. Scanned/encrypted PDFs and material outside retained text remain gaps.
6. With `NEWS_STRUCTURED_ENABLED=true`, the researcher returns a typed event brief directly. Claims carry
   source IDs and exact original-language passages. A separate tool-free model pass reviews entailment,
   dates, units, reference periods, translations, attribution and event status. Exact-passage and numerical
   checks run deterministically. Missing, duplicate or rejected verdicts cannot approve a claim. Metadata
   and interpretation require their own approval and all supporting claims to survive. Review is bounded
   to 180 seconds and at most 40 relevant documents / 100,000 retained text characters.
7. The final Markdown is rendered from accepted claims. It separates reported facts from conditional
   interpretation and direction from volatility. Partial collection is disclosed. Structured events,
   quotes, review decisions and coverage are saved under `reportQuality.intelligence`; existing report
   consumers continue receiving a Markdown string. A review failure cannot release the unreviewed draft.
8. Public non-social events are stored in `ai.news_events`. History is bounded to twenty recent relevant
   events; MACRO events are shared across assets. It is explicitly historical context requiring fresh
   evidence. Conservative grouping distinguishes repeated release dates, preserves contradictions and
   tracks corrections/retractions. Distinct URLs/domains do not establish independent corroboration.
   Current event records expire after thirty days; this is not a complete evidence archive.

Model review can be wrong. Exact quotation and successful retrieval do not establish factual truth or
predictive accuracy. Independent corroboration remains `not_established` unless separately proven.
Calendar surprise calculations require compatible units and reference periods; unavailable forecast,
actual or revision context remains unknown. Non-English evidence keeps original passages and language
metadata; translated claims are reviewed against originals rather than replacing them.

## Compatibility and limits

Compose enables structured research in `news-analyzer`, whose container remains `bitcoin-agent`. The model
remains MiMo. Standalone sessions default to legacy Markdown unless the flag is explicitly enabled.
Tool names, action interfaces and Markdown report delivery remain compatible. Migration 016 adds one
public-research table; the writer's existing startup migration/role provisioning handles access.

Research has a four-minute tool budget, up to twenty web searches and sixteen new article reads. The final
45 seconds are reserved for original reads/synthesis rather than new discovery. Dossier reads share the
article allowance. Public downloads reject private/reserved targets, validate redirects, cap bytes, and
keep time/call limits. Model synthesis and review have separate deadlines; four minutes is not a total
end-to-end runtime promise. Failed/blocked sources retain explicit status and bounded retry backoff.

## Public social bridge

The optional Compose profile `social-news` starts `news-social-export`. It has no network, API keys or
trading database configuration. It reads the existing X collector SQLite volume read-only and exports
only whitelisted public text, author, URL and publication/receipt fields to `news-state/social/x.jsonl`.
The analyzer receives these sanitized files, not collector account/session databases. Files older than
five minutes and posts outside the 48-hour window are rejected. Collector samples with at least three
relevant posts per platform can satisfy that discussion query without additional platform searches.

The exporter also supports `NEWS_REDDIT_DATABASE` pointing at a read-only mounted Reddit evidence store.
Only retained RSS observations are accepted; synthetic replay and erased revisions are excluded. The
latest retained revision wins. A live Reddit store must be configured separately; an adapter is not proof
of live platform coverage. No new Reddit access route or scraper is enabled by these changes. Social
claims are not copied into long-lived shared event memory. Existing saved analysis reports are historical
reports, not a live social erasure mirror.

## Configuration and tests

Model calls need `OPENROUTER_API_KEY`; persistent sessions and structured event memory need
`AI_DATABASE_URL`. `NEWS_STATE_DIR` enables shared public caching and collector snapshots.
`NEWS_SOCIAL_EXPORT_DIR` points to sanitized public JSONL files. Keep provider keys and connection URIs
server-only. Do not expose or commit environment files.

From `backend`, use the repository virtual environment to run:

```powershell
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m ruff check app automation_agent news_agent news_analyzer
```

Database tests require `TEST_LOCAL_DATABASE_URL` on a disposable PostgreSQL instance. The ETH catalog
suite additionally requires `TEST_ETH_CATALOG_DATABASE_URL` pointing to an `eth_catalog_test` database.
Apply migrations with `python -m scripts.init_local_db` in the isolated test environment first.

Breaking-news triggers, pre-activation news refresh, operational monitoring, historical evaluation and
complete reproducible evidence archives remain outside the agreed implementation scope.
