# News agent audit, 12 October 2026

The news agent gathers more varied evidence after the late-September overhaul, but its delivery is unreliable and its source coverage is incomplete. The latest BTC and ETH research completed at 00:30 IST on 12 October. Five configured collection endpoints still fail consistently, and Blockworks has a confirmed parsing defect.

This audit made no application, model, scheduler, credential, or trading changes. Live checks called public-source collection directly without calling a model or triggering an automation run.

After the audit, the user confirmed that failed agent runs were caused by the OpenRouter credit limit. Treat those outages as funding interruptions, not evidence that the news overhaul broke run delivery. The fetching, parsing, coverage, and report-quality findings below remain separate confirmed issues.

## Window and evidence

The review covers 12 September 2026 at 08:40 IST through 12 October 2026 at 08:40 IST, exactly 30 days. Jobs are assigned to the window using `started_at`, falling back to `scheduled_for`, then `created_at`. Report creation time alone is unsuitable because some report rows inherit the date a future job was created.

Read-only PostgreSQL queries inspected `trade.analysis_jobs` and `ai.analysis_reports`. There were 359 terminal jobs, including activation rechecks. Of those, 262 were main analyses that can run fresh news research; 97 were activation rechecks, which should not be counted as fresh news reports.

There are 169 saved news reports for main analyses in the window. Detailed `researchTrace` evidence exists for 56 reports, split evenly between BTC and ETH. The first detailed trace is from 30 September at 05:30 IST. Source failure statistics below describe those saved successful research runs, not every attempted run over 30 days. Older reports lack equivalent traces, and a news report can be lost if a later stage fails before the main result is saved.

Historical trace statistics were supplemented by direct BTC and ETH source checks on the deployed server, a live Blockworks parser reproduction, current service state, and a credential-safe OpenRouter allowance check. Raw exports stayed outside Git. Account identifiers and secrets are omitted here.

## When the improvements happened

| Date, IST | Change |
|---|---|
| 21 September | Search budgets, caching, bounded timeouts, and parallel collection improvements |
| 28 September | Public-source, discussion, and exchange discovery tools; agent-directed BTC/ETH research |
| 29 September | Async Agno tool dispatch, freshness handling, and preservation of feed publication dates |
| 30 September | Recent Google News discovery fallback and report normalization changes |
| 1 October | Relaxed report checks and increased research tool allowances |
| 5 October | Detailed debug logging and low news-model reasoning effort |

Relevant commits include `32ad903`, `f6edf11`, `dcc63b5`, `ab91bd5`, `5099c96`, `7959583`, `4fc6c73`, and `04e26c3`. Commit dates establish implementation dates. Saved traces demonstrate the broader collection tools operating by 30 September; they do not establish the exact deployment time of each earlier commit.

## Delivery before and after the overhaul

| Period, IST | Main analyses completed | Failed | Cancelled | Completion among attempted analyses |
|---|---:|---:|---:|---:|
| 12 September 08:40 through 27 September | 91 | 5 | 17 | 94.8% |
| 28 through 30 September | 27 | 5 | 0 | 84.4% |
| 1 October through 12 October 08:40 | 51 | 48 | 18 | 51.5% |
| Whole window | 169 | 58 | 35 | 74.4% |

These are end-to-end main-analysis results. A failed job is not automatically a scraping failure or a news-stage failure. The change in completion rate does not prove the overhaul caused the outages. The asset mix also changed: ETH became a regular participant late in the window.

- 4 October had eight failed main analyses and no saved news reports. The earlier [5 October investigation](2026-10-05-failures.md) confirmed OpenRouter HTTP 403 responses with `Key limit exceeded (total limit)` on 4 October and the 00:30 reviews on 5 October.
- Research resumed on 5 October and continued through the 8 October 05:30 reviews.
- From 8 October 12:30 through 11 October 19:00, 36 consecutive main analyses failed. There are no saved news reports for 9, 10, or 11 October in IST. The database preserves generic failure messages, insufficient to identify the original cause of this entire later outage.
- Both 12 October 00:30 main analyses completed. Both 05:30 reviews were cancelled because no trade slots were available. Those cancellations are scheduling decisions, not fetching errors.

Across all job types, 49 of 65 failures preserve only `The analysis failed. No strategy was activated.` Other failures include analysis timeouts, a disconnected server, missing selection reports, and a backend restart. Do not label all these failures as news errors.

The current OpenRouter key metadata returned a USD 25 limit, USD 22.853732358 usage, approximately USD 2.1463 remaining, and no automatic reset. The prior USD 20 limit has therefore changed, but the current allowance is still small. This check did not make a model request.

The analysis container was recreated during the audit around 08:39 IST. Its current Docker log starts at that restart. Historical detailed logs were not available from that container afterward, so the later outage's original provider error remains unconfirmed.

## Actual source coverage

Each asset's curator currently attempts 17 sources, including separate asset/global discovery requests. Across the 56 traced reports, it made 57 collection calls because one report collected twice.

| Source | Recorded collection failures | What the evidence means |
|---|---:|---|
| Federal Reserve monetary policy | 0/57 | Returned cards in every collection |
| SEC releases | 0/57 | Returned cards in every collection |
| ECB releases | 0/57 | Returned cards in every collection |
| CoinDesk | 0/57 | Returned cards in every collection |
| Federal Reserve all releases | 1/57 | Usually available |
| Forex Factory calendar | 1/57 | One historical HTTP 429; another 429 in a follow-up live check |
| Decrypt | 57/57 | HTTP 403 on the feed |
| Binance announcement listing | 57/57 | No usable items, even when retrieval returns a page |
| GDELT asset discovery | 57/57 | Connection timeout |
| GDELT global discovery | 57/57 | Connection timeout |
| Polymarket | 57/57 | Connection timeout |
| Google News global | 48/57 | Mostly empty results; some HTTP 404 responses |
| Google News asset | 4/57 | Mostly available |
| Blockworks | 3/57 explicit failures | The remaining calls return defective, stale cards; see below |
| Cointelegraph BTC | 1 explicit failure | Applies only to BTC collections |
| Cointelegraph ETH | 2 explicit failures | Applies only to ETH collections |

Both fresh BTC and ETH smoke checks reproduced the same five persistent failures: Decrypt, Binance listing, both GDELT requests, and Polymarket. The broader live query for global Google News returned cards, unlike many historical queries formed by concatenating several narrow topics.

BLS calendars returned cards in 43 of 57 historical collection calls without explicit source errors. A successful fetch can legitimately leave no events inside the requested time window. That must be distinguished from transport success, usable current coverage, and inclusion in the report.

The Ethereum Foundation and Bitcoin Core also return cards for their respective assets. Slow-publishing official sources can provide old background material. Old cards are not proof of fresh coverage.

The source list does not contain dedicated White House, BEA, Treasury, or CFTC collectors. Follow-up web searches can discover them, but do not guarantee coverage. There is no configured licensed breaking-news stream or structured calendar provider such as Trading Economics. The earlier architecture document describes an intended production stack, not a fully implemented one.

## Fetching and scraping findings

The saved traces contain 983 article-read outcomes, counting direct reads and dossier entries, including repeated and cached outcomes. Of these, 603 succeeded and 380 failed or were refused. This is a 61.3% success rate for recorded outcomes, not a unique URL or network-request success rate. Every traced report had at least one successful article read.

The 380 unsuccessful outcomes comprise:

- 117 pages without usable article text.
- 106 refusals after exhausting the 16-read allowance.
- 70 HTTP 403 responses.
- 43 article timeouts.
- 23 HTTP 404 responses and eight HTTP 401 responses.
- Six skips after a domain had already failed twice.
- Seven other outcomes, including rate limits, unsupported PDF/XML content, download limits, and server errors.

Successful outcomes concentrated on CoinDesk, Yahoo Finance, Federal Reserve, SEC, and a long tail of secondary publishers. Reuters and several other publishers blocked article retrieval. Thirty-five successful outcomes had no publication date in the trace. Missing dates limit freshness verification.

### Confirmed Blockworks defect

`parse_feed` understands namespaced Atom titles and publication dates, but looks for an unqualified `link` element. The live Blockworks feed is Atom, with `{http://www.w3.org/2005/Atom}link` elements. The parser misses the original article URL and resolves the absent link back to `https://blockworks.com/feed`.

All 54 historical collections containing a Blockworks card used that feed URL. The latest retained title was `Venezuela's sanctions are stablecoins' proof of concept`, dated 7 January 2026. A live reproduction returned the same defect. URL deduplication then collapses separate entries onto the feed URL. Blockworks should not be described as working current article coverage.

### Calendar selection loses useful events

The curator returns at most four cards per source and does not rank calendar entries by country or market impact before applying that cap. The latest Forex Factory selection contains a JPY bank holiday, a GBP speech, a USD bank holiday, and a CAD bank holiday. This can exclude later high-impact releases even when they are present in the downloaded calendar.

BLS provides separate coverage for its own releases, but cannot substitute for the entire global calendar. A later direct Forex Factory fetch returned HTTP 429, so this audit did not verify the full current week's payload against the four retained events.

## Research behavior and report quality

All 56 traced runs called public-source curation, news search, public-discussion search, and exchange-announcement search. Fifty-five also called web search. The newer agent is actively using the broader tools.

However, tools and citations do not enforce evidence quality on their own:

- News search returned its ten-call-limit error 44 times across 33 runs. Web search returned its twenty-call-limit error 18 times across 11 runs. Direct article reads returned the sixteen-call-limit error 30 times across 21 runs, with additional refusals inside dossiers. The model keeps requesting research after limits are reached.
- Of 1,051 distinct citation URLs counted separately per report, 577 exactly matched a successful article-read URL and 90 matched a curated source card without a successful article read. Eighty-two matched a failed read and no successful read of that exact URL. Thirty-eight were absent from retained read, card, and search-result URLs. These are provenance flags, not proof that every such claim was fabricated: traces truncate result lists, redirects change URLs, and reports sometimes explicitly label failed sources.
- Twenty-one of 56 reports cited Google News URLs despite instructions to locate and read the publisher article first. The latest BTC report includes a Google News discovery lead about Jane Street among its positive factors, although it labels the item as a discovery lead.
- Forty-nine of 56 reports contain nonempty text before their first Markdown section. Manual inspection confirms planning commentary such as `I'll start by gathering...` and `Let me verify...` in the latest BTC and ETH reports. The current pipeline only strips surrounding whitespace and checks for a completed, nonempty response; it does not enforce citation eligibility or remove this planning text.
- The latest BTC report has 15 distinct citations, ten matching successful article reads, two card-only matches, and one unmatched URL. The latest ETH report has 12 citations, 11 matching successful article reads. Neither latest report cites a failed-read URL without a successful exact match.

The latest reports have useful analytical behavior. ETH distinguishes an ETF asset-value decline from actual investor redemptions. BTC distinguishes a ceasefire announcement from confirmed Russian agreement. Both separate volatility risk from uncertain price direction and label some secondary or social evidence as unverified.

There is still inconsistent shared macro evidence. The latest ETH report flags reported government-shutdown delays and uncertainty over the BLS calendar, while BTC presents CPI as the main scheduled catalyst without the same qualification. This is a consistency finding about the saved reports, not external verification of either claim. BTC and ETH research runs should share a verified macro event record rather than independently resolving the same uncertain calendar.

No event-level accuracy calibration, forecast scoring, or market-reaction validation was established by this audit. A completed report and readable citations do not prove that its directional or volatility assessment predicts outcomes.

## Social and exchange coverage

Retained public-discussion snippets total 330 for X, 89 for Facebook, and 48 for Reddit. No retained snippets came from Threads or Binance Square. Site-restricted web search does not establish complete coverage or successful reading of original posts.

Exchange discovery returned retained leads for OKX, Coinbase, Kraken, Binance, and Bybit. There were no retained Delta Exchange leads. A searchable Binance announcement URL does not fix the failing Binance listing collector or prove complete announcement coverage.

The separate `social_monitor` project collects X evidence independently. The news/automation packages do not consume its database or alerts. Its existence therefore does not extend the news agent's coverage. See `social_monitor/README.md` for its separate lifecycle and limitations.

## Recommended order of fixes

1. Preserve original provider and news-stage errors on the job record, keep research traces when downstream strategy analysis fails, and retain logs across container replacement. Add allowance monitoring before the next provider-limit outage.
2. Fix Atom link extraction and distinguish available, stale, empty, blocked, and failed sources. Add a recent-card timestamp and last-success time for every configured source. Replace or disable consistently unusable connectors through supported source interfaces.
3. Rank calendar events by time, currency, and impact before limiting results. Share verified macro events and cancellation/delay status between BTC and ETH.
4. Make reports cite successful fetched originals or explicitly separated discovery evidence. Remove planning text and flag missing dates or unmatched citations before delivering the report.
5. Stop repeated requests after tool exhaustion, count cached reads appropriately, and reserve budget for reading primary evidence. Improve global queries instead of combining many unrelated phrases into one search.
6. Add the missing primary collectors and supported social/news providers according to the desired coverage. Measure publication-to-ingestion delay and evaluate event extraction and forecast accuracy on retained evidence.

Research breadth improved. Reliable delivery, current source health, and enforced evidence quality still need work before the system can be described as complete or dependable.
