# Repeated news-tool and payload audit

## What was tested

No model requests or end-to-end analyses were used. The audit calls the same seven public entry points
as the researcher, with normal research budgets and server networking. Three rounds use fresh tool
instances for BTC and ETH. Every call repeats once with the same arguments on that instance. This
produces 84 observations per audit, 12 per tool. Baseline and staged repair audits both completed.

The six search topics cover Bitcoin markets, Ethereum upgrades, crypto regulation/tax, government
economic/fiscal reports, tariffs/sanctions and war/energy. Actual tool JSON is retained in temporary
server artifacts, including source lists, article bodies, dates, errors and search results. Strict JSON
round trips reject NaN/Infinity. Raw article bodies are not committed to Git.

| Tool | Baseline populated responses | Staged repair populated responses |
| --- | --- | --- |
| curate_public_sources | 12 / 12 | 12 / 12 |
| build_news_dossier | 12 / 12 | 12 / 12 |
| read_news_article | 12 / 12 | 12 / 12 |
| search_news | 12 / 12 | 12 / 12 |
| web_search | 10 / 12 | 10 / 12 |
| search_public_discussion | 12 / 12 | 12 / 12 |
| search_exchange_announcements | 10 / 12 | 10 / 12 |

Populated does not mean correct, relevant, fully current or independently verified. Discussion includes
sampled X observations and discovery snippets; Reddit export was unavailable. Dossiers contain individual
failed publisher reads even when other originals succeed. No result establishes exhaustive coverage.

## Confirmed defects and changes

The baseline returned old news alongside current leads, and web results often lacked publication dates.
Future and undated results did not have explicit status. A concurrency reproduction sent 64 identical
searches through the ten-call allowance, yielding mixed successes and budget errors. A non-UTF-8 fixture
also reproduced damaged source characters under forced UTF-8 decoding. The saved live article samples
contained no replacement characters; apparent damage in a printed excerpt came from the terminal
display. Feed selection also assumed incoming ordering.

Searches now share identical in-flight requests, refresh successful cache entries after 60 seconds and
empty entries after ten, clean snippets to plain text, and expose normalized UTC publication dates,
reported dates, retrieval time, publication status and age. Unknown dates stay unknown. Current leads
precede older material, including when recent fallback results outnumber old provider results. Existing
success-array and error-object return shapes remain compatible with their consumers.

Articles preserve original bytes for HTML encoding detection and expose the same freshness metadata.
Inventory/extraction receives that metadata. Original financial quantities remain source text and do
not pass through numerical rounding. The researcher reuses prepared curation instead of being told to
repeat it. Newest dated source cards precede undated/background cards. Curation retains completed sources
under a 45-second deadline. Corrupt or oversized on-disk cache payloads are discarded and fetched again.

## Coverage expansion

The baseline source list expands from 35 to 44 connectors per asset, with bounded additional fallbacks.
Six dedicated discovery queries cover crypto law, tax/fiscal policy, country economic reports,
tariffs/sanctions/export controls, war/energy/shipping and regional crypto policy. Existing BTC, ETH,
macro calendars, central banks, publishers, exchanges, institutional disclosures and infrastructure
coverage remains in place. These broad queries find leads; originals remain required for factual claims.

Direct official connectors were verified against reachable public pages/feeds:

- [IRS current-month releases](https://www.irs.gov/newsroom/news-releases-for-current-month)
- [OFAC recent actions](https://ofac.treasury.gov/recent-actions)
- [EU sanctions guidance RSS](https://finance.ec.europa.eu/node/1296/rss_en)

All six new discovery categories returned available material in each of the 12 staged curation calls.
IRS returned available releases. OFAC and EU guidance had older releases and were correctly labelled
stale. Their lack of a new item during this window is not evidence that their connectors failed.

## Repeated load and failure tests

Five isolated repetitions each verify 64 identical searches, 32 readers of one original URL and 64
collectors sharing one feed. Every group makes one underlying fetch rather than consuming its allowance
with duplicates. Sixteen disk-cache instances also share one fetch. These groups exercise 816 concurrent
invocations with mocked upstream I/O. Public sites receive bounded live probes, not load floods.

The 26 focused tests also check cancellation, search expiry, stale/future/undated timestamps, original
non-UTF-8 characters, valid Unicode JSON, per-release IRS/OFAC dates, corrupt-cache repair, retention of
fast sources when another stalls, coverage configuration and fresh fallback ranking. These checks
establish the tested boundaries, not universal uptime or maximum production throughput.

## What the model receives

Successful search calls return a JSON array of URL, title, plain-text snippet, publication date,
reported date, retrieval time, publication status and age. They remain discovery evidence. A failed
search returns an explicit error object with an empty results list.

Article reads return a JSON object with `ok` and `article`. The article includes requested/final/canonical
URLs, title, publisher/author/language, publication and retrieval metadata, cleaned text, truncation
flags, up to three image references, source class and stable evidence ID. Targeted passages reuse retained
text. Dossiers combine individual successes and failures rather than pretending all requested URLs worked.

The prepared initial context contains bounded discovery leads, source health, calendars and actual
original reads. Extraction receives bounded originals and dates. Search snippets do not become verified
facts merely because they were serialized correctly. Existing passage checks and independent semantic
review remain required before accepted claims reach the strategy agent.

Freshness is measured at collection, subject to a one-minute source/search cache and subsequent model
runtime. Breaking-news triggers and pre-activation refresh remain deferred. A source can be unavailable,
undated or quiet, and no implementation can guarantee new data from every external call every time.

## Final regression checks

The full backend suite passed 1,152 tests with no failures or skips in 152.33 seconds against disposable
PostgreSQL on an internal network. It had dummy model credentials and no production database connection.
The final news suite passed 162 tests, including the 26 focused payload/load tests. Ruff and whitespace
checks passed. One existing Starlette/httpx deprecation warning remains. No frontend files changed.

## Deployed verification

Release `bef4a3b` was pushed and pulled on `ubuntu-server`. The analyzer and collector were rebuilt and
restarted after confirming zero running analysis jobs and zero active/pending strategies. Eight deployed
source files matched their committed contents. Writer, analyzer and BTC/ETH market services remained
healthy. No analysis or trade was started for verification. The disposable test database and internal
test network were removed.

The final deployed audit completed another 84 observations. Across baseline, staged and deployed audits,
252 actual public-tool invocations were recorded, with zero model calls. Each final tool ran twelve times:

| Tool | Final populated responses |
| --- | --- |
| curate_public_sources | 12 / 12 |
| build_news_dossier | 12 / 12 |
| read_news_article | 12 / 12 |
| search_news | 12 / 12 |
| web_search | 11 / 12 |
| search_public_discussion | 12 / 12 |
| search_exchange_announcements | 10 / 12 |

All payloads passed strict JSON round trips. The empty web response included an explicit provider
connection failure. Exchange results could contain a provider error even when another exchange supplied
a lead. The twelve successful sampled article reads were all labelled recent and included retrieval
timestamps. Six cached-repeat pairs preserved identical source text. No replacement characters were
present in the saved live samples, before or after the repair.

Final news-search payloads contained 38 recent and 14 stale leads. Stale leads were explicitly labelled;
the collector does not invent newer stories when a source cannot provide them. The six new discovery
categories returned material in all twelve final curation calls. IRS was available, while OFAC and EU
guidance remained correctly stale. Separate background-collector checks found BTC/ETH snapshots about
29 seconds old, with all six new categories present. These are observed collection results, not a
guarantee of exhaustive news coverage or proof of model reasoning quality.

Temporary validation artifacts are `/tmp/news-audit-baseline.json`, `/tmp/news-audit-repaired.json` and
`/tmp/news-audit-deployed.json` on the server. Local copies use the same filenames in the user's Temp
directory. They contain public tool payloads only. The final audit command is reproducible through
`scripts.audit_news_tools` without model or trading-database access.
