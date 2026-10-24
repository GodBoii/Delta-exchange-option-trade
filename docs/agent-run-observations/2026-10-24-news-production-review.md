# News production review, 24 October 2026

The deployed news research path has regressed. All three available post-deployment BTC news runs
returned an empty structured brief without making any research tool call. Each was recorded as completed,
then replaced with the same 231-character insufficient-evidence report. There are no accepted claims or
stored events from these runs. The collector and X export work, but their availability has not translated
into better production news analysis.

This review follows the chat "Map news agent architecture", ID
`01a15884-4483-7623-946d-f17b080551c4`, and its implementation/deployment records. Production inspection
used read-only database transactions, retained Docker logs, source-file hashes and existing public
snapshots. It did not invoke a model, trigger an analysis or trade, change settings, or restart services.
The only repository change is this review. All displayed run times below use IST.
The production evidence window ended at 10:29:31 IST on 24 October, with live service,
snapshot and integration verification continuing through approximately 10:42 IST.

## Deployment boundary and sample

The original news deployment finished on 23 October around 17:13 IST at `0610f8b`. The server was
subsequently updated to `a94f2cd91e5bed09a47e795ee65b7d3b19071e80`; the current writer/analyzer containers
started at 18:45:08 IST. Seven relevant deployed files match the server checkout byte for byte, including
`agent.py`, `pipeline.py`, `intelligence.py`, `coverage.py`, `social.py`, `collector.py` and the automation
team. Structured mode is enabled. This is deployed behavior, not an undeployed local change.

The read-only export covered 21 October through the inspection on 24 October. It contained fifteen
pre-deployment news responses, fourteen marked completed and one error. One of those older completed
responses was only sixty characters, so completion was already an imperfect quality signal.

There are six analysis reports after the deployment boundary. Three are main BTC analyses with news;
three are activation rechecks without fresh news research. No post-deployment ETH news analysis is
available in this sample. Rechecks must not be counted as additional successful news runs.

| Main BTC run | News tools | Retrieved citation URLs | Accepted claims | News elapsed | Later recheck |
|---|---:|---:|---:|---:|---|
| 23 October, 19:00 | 0 | 0 | 0 | 18.499 s | Dropped at 19:54 |
| 24 October, 00:30 | 0 | 0 | 0 | 16.824 s | Dropped at 00:43 |
| 24 October, 05:30 | 0 | 0 | 0 | 18.946 s | Dropped at 05:54 |

Main report IDs, in table order:

- `c1d2cbdc-92d9-445d-990a-139d675f6fed`
- `24952ee8-d023-4187-b3da-994891a5fa00`
- `7dab76d9-eeff-4dc8-9d30-e000c40a4323`

Their news run IDs are `bb608af0-2b73-400a-b7ae-18f699a02fab`,
`74a36fb8-d85f-4edb-ae05-1097cff5df29` and `87814ecc-70ce-4a03-b8d3-24708218b2c5`.

For comparison, the three immediately preceding news reports made thirteen, twenty-two and twelve
research tool calls and retained ten, eleven and twelve successfully retrieved citation URLs. Their
news reports contained 15,203, 19,883 and 17,924 characters. These URL counts establish retrieval
provenance, not semantic truth or predictive accuracy. The post-deployment outputs contain no findings
to assess for semantic accuracy. Their shorter execution time reflects skipped research and cannot be
credited as a performance improvement.

## Confirmed defects

### P1: An empty research response is accepted as successful analysis

The retained production logs show all seven research tools being registered. Each model response is
`{"events": [], "gaps": []}` and has no tool calls. Thus the empty research traces are not merely a
persistence/display issue. No original-read or semantic-review stage ran for these responses.

`news_agent/agent.py:150` sets `output_schema=Brief`. The model disables native structured outputs;
inspection of an actual SDK request with a local HTTP test transport shows seven function tools,
provider-default tool choice, and `response_format: {"type": "json_object"}`. `Brief` permits both arrays
to be empty. There is no mandatory first collection or code-level research-completeness check.

`news_agent/pipeline.py:293` takes the no-documents branch, inserts an evidence gap and leaves the
original completed status intact. The evidence sink at line 315 persists that completed status, and
the pipeline logs completion. A local reproduction using the real Agno/OpenAI HTTP serialization and
a canned empty provider response reproduced the complete failure without external network access:
one model response, no research calls, zero claims, and a completed insufficient-evidence report.

The combination of JSON-mode structured output and optional tool use is a plausible trigger for the
change in model behavior. This review did not make a paid provider A/B request, so it does not prove
that removing JSON mode alone fixes MiMo. The established application defect is accepting a run that
performed no research as a normal completed news analysis.

The automation integration at `automation_agent/team.py:86` proceeds with this result and supplies
only its rendered Markdown under current news evidence at line 179. All three strategy runs created
shared proposals despite the missing research. One report described the news state as "unknown/neutral".
Unknown coverage does not establish neutral event risk. The instruction to avoid selection on incomplete
evidence at line 161 is not a deterministic admission check.

All three subsequent rechecks returned `strategy_dropped`; proposal creation did not itself confirm
account entry. The recheck report IDs are `56cfdb3a-f935-49d5-adff-7b56dc92b023`,
`5486e252-df38-406c-8941-4f1bd0ffd59e` and `4593ed36-004b-48f4-b57e-a68a3f30d209`.
This is evidence that rechecks still work, not that selecting with missing news is acceptable.

The repair should distinguish no research from a researched run with no material findings, enforce
collection before accepting a research result, preserve a classified incomplete outcome, and make that
outcome explicit to downstream decision policy. Add a regression for the exact empty first response,
as well as an HTTP-level tool-dispatch test. A positive mocked tool call does not establish that the
real provider will choose to call a tool.

### P2: GDELT discovery replacements use the wrong parser

Both GDELT replacements are Google News RSS feeds with `kind=discovery`. Collection still dispatches
by `source.name.startswith("GDELT")` at `news_agent/sources.py:625`, which invokes the JSON GDELT parser.
Both deployed replacements consequently report `fetch_failed` with a JSON decoding error.

A local reproduction replaced fetching with valid RSS and used the actual replacement source objects
and curator. The RSS parser extracted one card from that payload; the curator returned zero cards
and JSON errors for both sources. This establishes a parser-routing bug independently of network
availability. The existing replacement test checks source configuration, not successful retrieval
through the selected parser. Dispatch replacement feeds by their actual format and test the curator
with both RSS replacements and direct GDELT JSON enabled.

### P2: The report hides specific evidence gaps

`news_agent/intelligence.py:223` checks whether the gaps list is nonempty but renders only a generic
sentence. It does not include the individual gaps. A direct call with a specific missing-calendar gap
confirmed that the detail is absent from the output. The three production reports therefore tell both
the user and strategy model only that some context is missing, while the stored quality metadata says
that no original document evidence was retrieved.

Render safe, bounded gap descriptions and a clear no-research state. Preserve the existing handling
of untrusted text; passing arbitrary model markup through unchanged would weaken the rendering guard.
Background discovery coverage and run-verified evidence should have distinct, visible provenance.
Currently coverage metadata inspects only curator tool executions, so the injected collector leads
are not reflected in its `not_collected` state.

### P2: ETH social-topic matching accepts unrelated words

`news_agent/social.py` matches topic terms as arbitrary substrings of post text. The topic
`Ethereum ETH` therefore matches the letters `eth` inside words such as "Whether" and names such as
"Sethi". A local sanitized-export fixture containing only "Whether traders agree remains unclear."
was selected as Ethereum discussion by the real reader.

A live reader sample contained eleven of thirty ETH selections without a standalone Ethereum/ETH
token. That count alone does not prove all eleven are irrelevant, since handles and other context can
matter, but the fixture establishes the lexical false-positive path. The thirty-post cap makes this
worth fixing: unrelated matches can displace relevant observations. Use explicit asset aliases and
token-aware matching, with tests for ordinary words, names, cashtags and meaningful asset mentions.
This defect did not cause the three empty reports because none called the discussion tool.

## What works, and what remains unproven

| Change | Observed result |
|---|---|
| Deployment and service readiness | Writer and analyzer healthy; zero restarts or OOM flags. Collector/export running with zero restarts; they have no Docker health probe. |
| Background collection | BTC and ETH snapshots were about 28 seconds old, with 97 and 93 discovery cards from 35 configured connectors each. Health contained 37 entries per asset because two fallback entries are reported separately. |
| X integration | Export was about 21 seconds old with 400 public records. The real sanitized reader returned thirty matching X observations for each asset topic. No social research tool was called by the three analysis runs. |
| Reddit integration | No live export is configured. The reader reports the unavailable export; adapter tests do not establish live coverage. |
| Broader feeds | Bank of England and Coinbase, Kraken and Circle status feeds returned available material. BIS and CFTC parsed successfully but had older releases. Quiet official feeds should not be classified as broken solely because their releases are old. |
| Remaining source limits | Both Bank of Japan feeds timed out, Blockworks timed out, Binance discovery was empty, and both GDELT replacements failed parsing. ETH institutional discovery was empty at inspection. |
| Claim safeguards | Empty/unreviewed findings were not released as facts. Focused tests exercise exact passages, numbers, duplicate verdicts, metadata approval and review failure. With zero production candidates, improved semantic judgment is unmeasured. |
| Event memory and grouping | Table exists and the analysis role has select/insert/update privileges. It contains zero events. Persistence is exercised in the isolated database suite; production retention, corrections and grouping have not been demonstrated by these runs. |
| Market capture ordering | The three market packets were captured after news completion, at approximately 19:00:38, 00:30:34 and 05:30:36 IST. The ordering fix is visible in production. These short empty news runs do not stress a full-duration research case. |
| Rechecks | All three ran and dropped the proposed strategies. They do not run fresh news research, as the agreed design specifies. |
| Market-data service state | Both BTC and ETH services currently report healthy. One retained 19:53 recheck packet did report incomplete minute history. Current health does not imply uninterrupted historical feed quality. |
| Budget reservation, topic queries, targeted PDF reads, translation, release comparison | Implemented paths have passing local tests, but the post-deployment research runs never exercised them. No production improvement can be inferred yet. |

The observed collection health was twenty-three available entries for BTC and twenty-two for ETH.
Other entries include old but readable releases as well as failures; this is not a twenty-three-of-thirty-five
verification rate. Discovery cards and social observations are not independently verified market events.
The last 350 collector log lines contained no Python exception traceback. The exporter had no retained
log output, so export freshness and reader behavior provide stronger evidence than log silence.

## Additional integration finding outside the news changes

The full database run exposed two account-capacity failures in `tests/test_local_runtime.py`:
`test_four_quarter_allocations_skip_analysis_and_release_restores_capacity` and the
`100-True` case of `test_shared_capacity_uses_other_accounts_when_owner_is_full`.
Their connected-account fixtures omit `account_name`. The newer funding preview at
`app/account_manager.py:136` indexes that display field directly, so the capacity path raises
`KeyError`, and the shared-capacity path reports availability as unknown.

The normal credential writer supplies an account name, and a read-only production shape query found
that all three connected main-account records currently contain both an account name and Delta ID.
This is a reproducible missing-field failure and test-contract mismatch, not an observed outage of those
three production accounts. Resolve whether the reader must tolerate legacy records without a display
name or the integration fixtures must satisfy the newer credential contract. Keep this separate from
the confirmed empty-news regression. No account record was altered for the review.

## Verification

- Focused news/analyzer/integration tests: 188 passed.
- Full Windows backend/news suite: 949 passed, 140 skipped because isolated database URLs were absent.
- Frontend: 100 tests passed; TypeScript typecheck passed.
- Ruff passed across app, automation_agent, news_agent, news_analyzer and the reviewed exporter/memory test.
- HTTP-level empty-response reproduction confirmed that the no-research run is marked completed.
- Curator reproduction confirmed that both RSS GDELT replacements are routed to the JSON parser.
- Renderer reproduction confirmed that individual evidence-gap descriptions are discarded.
- Sanitized social-export reproduction confirmed that "Whether" incorrectly matches ETH.
- Final isolated Linux/PostgreSQL suite: 1,087 passed, two failed, zero skipped in 143.44 seconds.
  The two failures are the missing-account-name capacity cases described above. Event-memory,
  ETH catalog, worker deadline and provider-error propagation tests passed in this final run.

The isolated runner mounts only source/test directories and project test configuration read-only,
uses an internal Docker network and disposable PostgreSQL with fake credentials, and has no production
environment file or trading database connection. The first two attempts stopped before tests because
the restricted test-container user could not read migration 015, which is owned by the host user with
mode 0600. The final runner uses that same numeric host UID; production permissions were not changed.
An initial test invocation through `pytest.main` in a stdin script also caused three multiprocessing
tests to fail because spawned children could not reopen `<stdin>`. The corrected invocation runs
`python -m pytest` as a separate process. These harness failures must not be counted as production
worker failures.
The corrected full run cleared all three multiprocessing failures. Disposable audit containers and
their internal network were removed after the run.

## Repair order and acceptance criteria

1. Correct the no-research success path and verify provider tool behavior without weakening passage
   validation. Test an empty first response, a legitimate researched no-findings response, provider errors,
   rejected claims and successful research. Decide downstream incomplete-evidence behavior explicitly.
2. Correct RSS replacement parser dispatch and retain separate statuses for quiet feeds, empty discovery,
   network timeouts and parser errors. Reassess Binance and Bank of Japan availability after that fix.
3. Expose specific sanitized gaps, distinguish collector discovery from verified run evidence, and
   correct social topic matching before assessing relevance or sentiment quality.
4. Run a news-only provider smoke for both assets after repair, without scheduling or trading. Require
   actual tool executions and original retrieval, and inspect accepted/rejected claims and any saved events.
   Zero accepted claims can be legitimate after research; zero research is the regression to catch.
5. Assess the next normal production analyses. Check reviewed claims against their retained quotes,
   memory continuity, meaningful coverage disclosure, research latency and downstream interpretation.

The previously deferred breaking-news triggers, pre-activation news refresh, monitoring, historical
scoring and complete evidence archives were not treated as implementation failures in this review.
Production news quality is not verified as improved. The confirmed defects remain present at the
reviewed deployment; infrastructure health and passing mocked tests do not clear them.
