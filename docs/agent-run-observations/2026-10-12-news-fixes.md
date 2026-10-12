# News coverage and reliability fixes, 12 October 2026

The audited parsing, calendar selection, report provenance, research-budget, and evidence-retention defects are fixed. Public collection now attempts 25 connectors per asset, up from 17. The user confirmed that the earlier failed agent runs were credit-limit interruptions, separate from these defects.

## Changes

- Atom entries retain their original article links. Missing links do not become feed URLs. Blockworks remains explicitly stale when its feed serves January material, with current public discovery attempted separately.
- A clear product user agent and standard HTTP content negotiation restore Decrypt feed and article retrieval. Response MIME types, public-address checks, redirect limits, and download limits remain enforced.
- Dedicated BEA, Treasury, White House actions/statements, CFTC, The Block, Kraken, and Delta connectors expand coverage. Treasury and Delta listing dates are retained and navigation links are excluded.
- Exchange discovery searches all six supported exchanges. Social and exchange results must match the selected domain and path. Threads discovery uses `threads.com`, following [Meta's documented domain migration](https://about.fb.com/news/2025/04/new-features-threads-web-experience/).
- Calendars retain up to sixteen events, ranking impact first and preferring USD within the same impact level. Important global events rank ahead of minor U.S. events. Concurrent workers share recently fetched source snapshots under OS locks.
- Cached searches and article reads do not consume fresh-request allowances. Individual missing pages no longer disable an entire publisher. Known blocked domains do not spend another download allowance. Site searches can recover indexed originals without a date filter, while keeping undated results labelled as leads. Empty searches are distinct from transport failures.
- Bounded PDF extraction supports original documents. HTML access challenges are rejected as article evidence.
- Planning prefixes are removed and unfinished tool markup is rejected. Citation eligibility uses full successful read results, including redirects, or retrieved calendar entries. Google News pages cannot count as original evidence even if they contain readable text. Unsupported citation blocks are omitted with an evidence-gap notice.
- News results and bounded failure details commit before later analysis can fail. Recovery does not overwrite them with empty member responses. Credit errors retain their classification across worker processes and have a clear run-history message.
- The private `news-state` volume stores public source snapshots and bounded application logs across container replacement. Logs are retained for thirty days, use separate files per process, and redact configured secrets. Concurrent retention cleanup tolerates files already removed by another worker.

## Verification

181 focused regression tests passed, covering news tools, real Agno async dispatch, provenance, provider-error propagation, automation scheduling, and activation rechecks. Ruff and whitespace checks passed. Additional isolated PostgreSQL verification proved news remains committed after a downstream failure and snapshot updates preserve it. A Linux test with separate Python processes proved the same public source required one fetch.

Live BTC and ETH collection checks each attempted 25 connectors and initially returned 61 and 59 cards. Both received matching upcoming calendar entries, with CPI and PPI ahead of holidays. Original pages from BEA, White House, Treasury, and Delta read successfully. Decrypt returned recent feed cards and readable original articles.

A standalone live BTC news run completed in 158.54 seconds. The cleaned report began with its report heading, contained no Google News citations, and retained fifteen eligible evidence URLs. Five unsupported citation blocks referring to four URLs were omitted. This test ran news research without invoking strategy selection or order tools.

A post-deployment collection returned 62 cards across 25 connectors, with explicit health for six discovery fallbacks. The original Decrypt feed was available. The deployed article reader rejected a Google News discovery URL without spending a download allowance. Analyzer health reported `success=true`, `databaseReady=true`, and no database error. The trading backend also reported healthy. Persistent state was writable and retained log files were present.

Application changes were deployed through code revision `c3b29b3`. The analyzer and backend were rebuilt and replaced; market-data containers were not replaced. Changes were committed one file at a time and pushed to `main`.

## Remaining external limits

Binance's listing can return an empty response, CFTC can block direct retrieval, GDELT and Polymarket can time out, and Blockworks can serve a frozen feed. Their status stays visible while public discovery alternatives are attempted. An alternate discovery lead does not establish complete coverage or become a verified claim without an original read. Some fallbacks also return no results or fail.

Public social discovery remains incomplete and does not consume the independent X monitor's database. Licensed news streams and authenticated social providers need separately configured access. These changes verify retrieval provenance and improve source coverage; they do not establish sentence-level factual accuracy or calibrated market forecasts.
