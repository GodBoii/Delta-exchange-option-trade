# Analysis latency and concurrent runs

The main decision model uses `high`. News synthesis and activation rechecks use `low`.
No live strategy was scheduled while validating these changes.

## Execution

News research and market collection start together. Research performs three bounded searches,
opens up to ten distinct article URLs, removes copied article bodies, and makes one synthesis
request. The main decision agent receives that report once. It no longer delegates repeated
research through an Agno Team. Saved results retain the existing `memberResponses` field so
the news report remains available to current readers.

Rechecks are independent jobs, scheduled at activation minus seven minutes. They retain fresh
market collection and the two short-term charts. The model must return an explicit validated
decision. An inconclusive response without a recorded cancellation cannot confirm entry.

An isolated worker process enforces the 300-second recheck deadline, including market I/O,
model inference, and persistence. The caller allows ten additional seconds for cleanup and
the response. A timeout terminates local work; it cannot guarantee that an upstream model
provider stops billing an already submitted request. Committed actions remain recoverable
through the existing database outcome checks.

Run claiming is now atomic per run ID rather than exclusive per account. Both Convex and
PostgreSQL allow independent runs concurrently. Each run still has one terminal action and
can only be claimed once. Manual shared requests also create distinct runs.

The scheduler reserves separate capacities for regular analyses and rechecks. Defaults are
three analyses plus four rechecks, configurable with `AUTOMATION_ANALYSIS_CONCURRENCY` and
`AUTOMATION_RECHECK_CONCURRENCY`. These are machine capacity limits, not per-account locks.
The scheduler still polls every 30 seconds, so actual starts depend on polling and available
capacity. Gold and ETH analysis logic are not implemented by this change.

## News tools and limits

| Operation | Bound |
| --- | --- |
| `search_news`, `web_search` | 10 calls each per research instance; 10 results; 20-second tool deadline |
| Native DDGS request | 10-second network timeout |
| `read_news_article` | 10 calls total, including reads inside dossiers; 30-second deadline including queueing |
| `build_news_dossier` | 10 calls; up to 10 unique URLs per call; 60-second batch deadline |
| Fetch concurrency | 4 per research instance |
| Article response | 4 MiB decompressed, 5 redirects, 8,000 text characters, 3 image references |
| Research stage | 90 seconds shared across tools |
| News synthesis | One model request, 90-second request timeout, no SDK retries, 6,000 completion tokens |
| Standalone news endpoint | 180-second worker deadline |
| Main model | 180-second request timeout, no SDK retries, 12,000 completion tokens |
| Recheck model | 240-second request timeout, no SDK retries, 4,000 completion tokens, within the 300-second job deadline |

Research instances are created for each analysis. Cached calls still consume their per-tool
allowance. Searches are deduplicated by query and URL. A dossier shares one HTTP client,
downloads identical URLs once, returns partial successes, and stops attempting a domain after
two failures in the same instance. Failed pages remain explicit evidence gaps.

Copied bodies require at least 85% matching five-word shingles over substantial text before
being merged. Alternate source URLs remain attached to the retained article. Similar headlines
alone do not remove independent reporting. No copied bodies met that threshold in the live audit;
tests cover copied and independent versions of the same event.

Agno's keyless `WebSearchTools` remains the search provider. Native Newspaper4k, Website,
Trafilatura and Crawl4AI were considered. Adding another downloader would duplicate the existing
validated fetch path. Browser crawling adds rendering work. The existing text parser already
provides dates, canonical URLs and article text without another model request.

- [Agno WebSearchTools](https://docs.agno.com/tools/toolkits/search/websearch)
- [Agno toolkit index](https://docs.agno.com/tools/toolkits/overview)
- [Agno Crawl4AI](https://docs.agno.com/tools/toolkits/web-scrape/crawl4ai)
- [Agno async execution](https://docs.agno.com/agents/running-agents)

## Source measurements

`news-source-benchmark.json` contains the latest measurements from this workstation. The audit
uses every distinct fetched URL found in `agent_run.txt` plus a fresh DDGS news search. Production
URLs are discovered dynamically, so this is an observed sample rather than every future URL.

The initial audit tested 20 URLs and extracted usable text from eight. After enabling a one-day
news search filter, the repeated audit tested 15 URLs and extracted usable text from four.
The retained audit measured search at 1.062 seconds, successful downloads at 1.155 to 1.583
seconds, parsing at 106 to 216 milliseconds, and JSON serialization at 0.04 to 0.12 milliseconds.

Most failures were HTTP 403 responses. Blockonomi connections failed. MSN returned a page shell,
and FinanceFeeds returned too little article text. Those pages are excluded from usable evidence;
the implementation does not attempt to bypass access restrictions. A single workstation audit
does not justify permanently blocking an entire publisher.

Clean text is the default. Successful Yahoo article pages need no screenshot. Page shells such
as MSN are candidates for a future browser comparison, but screenshot capture and image-model
cost were not benchmarked here. Screenshots are therefore not automatically substituted for
failed pages. Serialization is too small a measured cost to justify that change.

Reproduce without model calls from `backend`:

```powershell
$env:PYTHONPATH = '.'
.venv/Scripts/python.exe scripts/benchmark_news_sources.py --log ../agent_run.txt --output ../docs/news-source-benchmark.json
```

## Deployment and verification

Deploy the analyzer, backend scheduler, and Convex functions together. For PostgreSQL runtime
installations, apply migration `024_concurrent_analysis_runs.sql`. For Convex, invoke the
authenticated `runtimeControl:reschedulePendingRechecks` mutation with `cursor: null`, then its
returned cursor until `isDone` is true. It only moves pending rechecks whose seven-minute start
is still in the future. Running and imminent jobs remain unchanged.

Regression tests cover per-tool budgets, nested dossier limits, concurrent downloads, cache reuse,
duplicate bodies, private redirects, oversized responses, cancellation, process termination,
independent claims, reserved recheck capacity, schedule migration and inconclusive rechecks.
Local verification passed 309 Python tests and 32 Convex tests, TypeScript checking, targeted
Ruff checks and `git diff --check`. Database migrations and production deployment have not run.
Logs now separate fetch, parse, tool and model token usage. End-to-end model speed and deployed
server capacity still need measurement after rollout; the source audit does not establish those.
