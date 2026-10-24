# News repair verification, 24 October 2026

The earlier regression was application behavior: a research agent with an optional empty output schema
returned empty briefs without using tools. The pipeline accepted those responses and allowed strategy
selection. Passing mocked model dispatch tests did not establish real provider tool selection.

## Implemented behavior

- Research keeps Markdown output and all seven public tools. The pipeline collects sources and attempts
  six balanced originals before inference, favoring fresh asset leads and different publishers. These
  executions share the researcher's cache and budget and retain their origin in persisted traces.
- Extraction and review have separate prompts, bounded input and output, and local schema validation.
  Verification cannot release the unreviewed report. Exact passages, quantities and metadata approval
  remain required. Empty findings need an explicit limitation.
- Structured strategy selection requires verified non-discussion findings, original article retrieval
  and accepted claims. No research, calendar-only evidence, discussion-only evidence, invalid output
  and failed verification do not pass this admission check.
- GDELT discovery replacements use the RSS parser. ETH/BTC social matching uses token boundaries.
  Specific gaps and source limitations survive rendering. Cancelled downloads can retry within budget.
  Undated recurring reports require stronger identity evidence before merging.
- Legacy connected accounts may omit the display name without breaking funding preview. Credentials,
  account identity and funding admission rules retain their existing behavior.

## Tests and direct checks

The regression tests use actual Agno request construction and HTTPX MockTransport, dummy credentials
and no external model requests. They cover the empty provider response, missing originals, invalid
extraction, retained failure traces, review approval, strategy admission, parser dispatch, cancelled
downloads, numerical formatting, recurring events, social matching and legacy account compatibility.

The final backend suite passed 1,126 tests with zero failures or skips in 149.29 seconds, against
disposable PostgreSQL on an internal Docker network. It had no production database connection or usable
model credential. Ruff and Git whitespace checks passed. The 37 focused production regressions passed.
The suite retains one existing Starlette/httpx deprecation warning. Frontend validation previously passed all
100 tests and TypeScript checking; this repair changes no frontend files.

Direct public-tool probes exercise curator, dossier, article passage, news search, web search,
discussion and exchange search. The initial BTC/ETH probes each read four originals; the final ETH
selection probe read three, attempted a current Ethereum upgrade report and used an available Delta
article alongside official releases. Some publisher reads failed or were blocked. Targeted reads
reused downloaded text. GDELT replacement feeds returned available discovery cards for both assets.
News search returned results; general web search was empty in the earlier probe and returned results
in the later ETH probe. X export observations were available. Reddit reported an unavailable export.
These checks made zero model calls and do not establish analysis accuracy or complete coverage.

## Limits

Earlier live model attempts timed out during extraction, then hit account credit limits. The user
explicitly stopped paid testing. There have been no further paid checks after that instruction, and
the completed model path has not been verified against the real provider. Future user-initiated runs
must establish relevance, accepted-claim quality and total runtime. No trades or analyses are started
for deployment verification. Breaking-news triggers, pre-activation news refresh, monitoring,
historical evaluation and full archives remain deferred.
