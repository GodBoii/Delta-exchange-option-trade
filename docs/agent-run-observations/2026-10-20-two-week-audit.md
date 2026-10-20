# Agent and execution audit, 20 October 2026

The system is operating, but it is not fully healthy. The recent strategy drops are committed activation-recheck decisions. They are separate from provider failures, capital admission, and account entry failures. Several defects make the results less reliable and harder to understand.

## Evidence and scope

Read-only production PostgreSQL inspection covered approximately 6 October 08:54 IST through 20 October 08:54 IST. Jobs were selected by `coalesce(started_at, scheduled_for, created_at)`, excluding future jobs. The export contains 173 jobs, 121 associated report rows, 77 proposal rows including account copies, and 34 strategies created within the window. Proposal counts are not counts of independent AI selections.

The investigation also inspected retained analyzer and trading logs, current private health endpoints, provider key allowance metadata, deployment history, current code, and the signed-in production UI. Raw exports remain outside Git. No analysis, approval, order, transfer, capital change, automation switch, production database update, or service restart was triggered.

All times in this report are IST unless explicitly labelled UTC. Historical logs are incomplete before persistent logging was deployed on 12 October. Saved traces can truncate individual results. A missing trace is not evidence that a tool was never called.

## Two-week delivery

| Job type | Total | Completed | Failed | Cancelled before completion |
|---|---:|---:|---:|---:|
| Main analysis, including manual and follow-up | 130 | 49 | 41 | 40 |
| Activation recheck | 43 | 40 | 2 | 1 |
| All | 173 | 89 | 43 | 41 |

Among main analyses that completed or failed, completion was 49/90, or 54.4%. Capacity and pause cancellations are excluded from that denominator. Completed main analyses selected 43 strategies, requested three follow-ups, and chose no trade three times. Completed rechecks approved 30 and dropped 10.

BTC had 41 completed, 23 failed and ten cancelled main analyses. ETH had eight completed, 18 failed and 30 cancelled. ETH automation is currently disabled globally. The signed-in account's participation switch is also off, independently of global BTC analysis. These states were read, not changed.

Completed main analyses had a median elapsed time of 507 seconds and a maximum of 1,350 seconds. Completed rechecks had a median of 50 seconds and a maximum of 128 seconds. These are whole-job elapsed times, not provider response latencies.

The cancellation records contain 26 `No trade slots are available`, seven `ETH automation was turned off`, and eight stale reviews. Two stale reviews retain `analysis_capacity_pending`, two retain `start_window_expired`, and four older records lack a specific expiry reason. The separate 12 October scheduler audit documents the legacy null-settings admission defect and its deployed fix.

## Why the latest strategies were dropped

The last four completed rechecks all called `drop_strategy` successfully. The tool responses contain `outcome=strategy_dropped`, and the associated shared proposals are cancelled with the supplied reason. No recent valid preview returned an evidence-invalid result in the stored tool summaries.

| Recheck | Assigned entry | Strategy | Recorded reason |
|---|---|---|---|
| 19 October 15:13 | 15:20 | Short OTM call | Actual strike 83,400 was only 0.45% above spot, quoted spread was about 19.9%, selected IV was below the compared realized volatility, and the book had turned bid-heavy. |
| 19 October 19:23 | 19:30 | Short strangle | Actual strikes 81,800/82,600 were inside recent excursions; sideways score fell to 66.55 and price/flow weakened. |
| 20 October 00:53 | 01:00 | Short strangle | The spot range thesis survived, but the model rejected narrow strikes, IV below its realized-volatility comparison, and the estimated adverse-move exposure relative to credit. |
| 20 October 05:43 | 05:50 | Short strangle | Spot broke the earlier tight range, sideways score fell from 87.73 to 63.94, selling increased, and the put was only 0.45% out of the money. |

It is not literally every strategy. The 19 October 00:53 recheck approved and the trade subsequently completed. The 11:23 recheck failed with `Activation recheck model failed`; that was not a confirmed drop. A separate 12:30 main review hit the provider key limit. Two later manual attempts failed, one with a generic stored error and one with `ConnectTimeout`.

### The main selection and recheck evaluate different evidence

`backend/automation_agent/team.py` explicitly tells the main agent not to infer current option prices and leaves actual option suitability to the recheck. The main agent chooses a saved shape and a schedule from spot/news evidence. The recheck resolves actual contracts and evaluates premium, IV, Greeks, fees, strike placement and advisory liquidity.

That division is producing demonstrable mismatches. On 19 October the main agent described its two-strike OTM short call as roughly 1.2% or more above spot and above the next resistance band. The actual recheck strike was only 0.45% away. A short strangle described as covering a broad market range similarly resolved to strikes roughly half a percent away. Two listed strike steps do not imply a fixed percentage distance or placement beyond the reported support/resistance.

The main thesis, selected strike geometry and actual option economics therefore do not always agree even when little time has elapsed. This is a design weakness in candidate selection, not proof that a failed news tool caused the cancellation. A better candidate evaluation should show actual listed-contract geometry and clearly labelled indicative economics before proposing entry. Fresh recheck and account-sized execution checks should still remain mandatory.

### Main market evidence ages while news runs

The main workflow captures market data concurrently with news research, then waits for news before starting strategy analysis. `MarketIntelligenceTools.market_packet_json` returns `_packet_cache` on every subsequent market-tool call; there is no age-based refresh in that method. Charts and numerical evidence therefore continue to describe the early capture.

For the last four selected main runs, market snapshots were approximately 345, 428, 506 and 501 seconds old when the main job completed. The corresponding recheck snapshots were only 15, 55, 37 and 21 seconds old at completion. Today's Asia snapshot was captured at 05:30:30, the main job completed at 05:38:51, and recheck captured fresh market evidence at 05:43:39. The changed sideways score compares observations about 13 minutes apart, not a refreshed main observation against an immediately subsequent recheck. Yesterday's New York comparison spans about 23 minutes.

This stale-cache behavior is a confirmed contributor to differing inputs, although it does not prove every cancellation was avoidable. Refresh market evidence after news completes and apply an explicit freshness policy before selection. A tool described as current should not indefinitely return the run's initial capture.

### Model changes coincide with the increased drop rate

The first started job using `deepseek/deepseek-v4.1-flash-1017` in this export is the 19 October 00:30 main review. Its six rechecks have one approval, four drops and one failure. The earlier model has 29 approvals, six drops, one failure and one capacity cancellation across 37 rechecks. News used Flash 1017 for three retained reports before switching back to `xiaomi/mimo-v2.6-pro`; current analyzer health confirms MiMo for news.

The new model is a plausible contributor to changed judgement. Six observations, different markets and changed evidence do not establish causation. No controlled replay or model comparison was run, and no automatic rollback was made.

### Some recheck explanations are wrong

The 20 October 05:43 report says the breakevens lie within one seven-hour scaled move. Its own values put the breakevens about 0.79% below and 0.87% above spot, versus movement scales of 0.70% and 0.72%. The strikes are inside those scales; the breakevens are outside. It also claims a 100% credit stop would trigger on about a 0.3% move, although the supplied local 0.5% move estimate loses only about $0.08 against $0.279 credit. That stop-distance statement is unsupported by its own evidence.

The $0.279 credit is for a normalized unit with 0.001 BTC per contract. It is not the account's total premium or budget. A small absolute credit alone cannot establish a poor return. Entry fees for that example are $0.009765, or 3.5% of credit, excluding tax and exit costs. Other reports correctly normalize amounts, so unit handling is inconsistent rather than universally broken.

Older approvals also claim software stops cap unbounded risk in practice. The prompt correctly says software stops do not bound expiry payoff. Those model statements should not be treated as proven protection through gaps or outages.

## Confirmed defects and operational degradation

1. **Expired-contract recovery loop.** Two 17 October short ATM straddle entries remain in `attention` after `Contract already owned by another strategy`. `resolve_unsubmitted_run` checks account-wide exposure and then requests each expired product's position. Delta returns HTTP 400 `expired contract`; the exception prevents the terminal cancellation. Retained logs contained over 13,000 reconciliation errors for these two rows and were still accumulating during inspection. Neither row has executions, order intents, reserved capital slots or product claims. They are not proven to block current capital, but they create repeated exchange requests, persistent false attention and log churn. Fix expired-product reconciliation without treating every failed position lookup as zero exposure.

2. **Material historical risk-monitor outage.** On 18 October approximately 01:02 through 01:22, retained logs contain 545 combined-risk-monitor failures due to Delta HTTP 502, followed by 31 `unavailable` failures through about 01:23. This is a period when local risk evaluation could not complete. Additional brief failures concern missing/incomplete entry fills and a fill/position disagreement. The latest such retained monitor error was 19 October 02:36. Current health reports zero risk failures and there are no live strategies at inspection; that does not erase the historical outage. The inspection did not prove whether exchange-hosted protection covered every affected interval.

3. **Committed recheck decisions are misreported.** `committed_action_text` handles main-analysis outcomes but omits `strategy_reconfirmed` and `strategy_dropped`. All 40 completed recheck reports in this export contain the fallback `No trading action was confirmed during this review.` The backend overwrites their Decision sections with that message. Production UI visibly shows `Strategy Dropped` alongside the contradictory fallback. Direct calls reproduced the defect for both outcomes. Fix report normalization and historical display without changing approvals or cancellations.

4. **Proposal lifecycle remains stale.** Across the whole production database, 58 shared proposal rows and two account proposal rows still say `scheduled` despite past activation times. The production UI shows yesterday's failed recheck proposal as Scheduled. A scheduled proposal is not evidence that an executable strategy is queued: there were no overdue scheduled/running analysis jobs and no live/scheduled strategies at inspection. Reconcile proposal lifecycle with failed rechecks, account allocation and terminal execution status.

5. **Provider allowance remains a delivery constraint.** The 19 October London failure is confirmed in retained logs as `Key limit exceeded`, with the then-current $25 key limit exhausted. Current authenticated metadata shows a $28 limit, about $25.1936 used, about $2.8064 remaining and no automatic reset. This is key allowance, not a statement of the user's overall account credit. Readiness checks do not test available allowance. The earlier 36 consecutive failures from 8 October 12:30 through 11 October 19:00 retain generic errors; prior audit records user-confirmed funding interruptions, but original per-run provider evidence for that entire block cannot be recovered.

6. **Recheck provider errors lose detail.** Both failed rechecks store a generic analysis failure. The retained 19 October trace raises `Activation recheck model failed` after the SDK reports error status, discarding the original cause at that boundary. The exact underlying provider error is unproven. News failures preserve a credit classification better than this recheck path. Public run-error filtering also intentionally hides unrecognized diagnostic messages behind a generic summary.

7. **News provenance filtering is incomplete.** `clean_report` removes blocks containing unread citations, but retains uncited factual paragraphs. An offline reproduction retained an unsupported numerical claim while deleting its source block. This is a verified enforcement gap, not proof that every uncited statement is false. The latest report still has uncited factual assertions in its summary, so the evidence-gap notice must not imply all unsupported claims have been removed.

## News research and tool performance

Forty-nine retained news reports have research traces. They include 52 curator calls, 314 news searches, 275 web searches, 285 direct article reads, 100 dossier calls, 94 public-discussion searches and 47 exchange-announcement searches. These are stored trace entries, not unique outbound requests; caching and repeated collection affect that distinction.

Thirty-five curator calls contain the newer source-health records. Their recurring limitations are:

| Connector | Observed status in those 35 calls |
|---|---|
| CFTC original feed | Blocked in 35/35 |
| Binance announcement listing | Empty in 35/35 |
| GDELT asset and global | Each timed out in 35/35 |
| Both GDELT discovery fallbacks | Each failed in 35/35 |
| Polymarket API | Timed out in 35/35; discovery fallback available |
| Blockworks original feed | Stale in 30, timed out in five; discovery usually available |
| BEA feed | Older than requested window in 35/35; that does not by itself prove a broken release feed |
| CoinDesk, Decrypt, The Block, BLS and Forex Factory calendars | Available in 35/35 |

The latest curator returned 69 cards from 25 configured primary connectors, plus separately reported fallback health. Those cards are discovery evidence, not 69 verified articles. Its dossier calls successfully read five of ten requested originals. Across retained dossier metadata, 254 of 477 requested articles succeeded. Failed or inaccessible originals remain a substantial gap.

Trace errors include 44 web searches with no results, 44 article reads with no usable text, 19 news-search call-limit responses, 15 article-read call-limit responses, seven web-search call-limit responses, and additional research-deadline responses. Empty search, blocked publisher, deadline and exhausted allowance are different failure classes. They are not all provider failures. Repeated requests after a tool's budget is exhausted waste model steps.

Thirty-two reports contain quality-filter metadata. Twenty-two removed at least one block; 128 blocks were omitted in total, with 120 unsupported URL entries counted across reports. These are report-local counts, not 120 unique global URLs. This proves the filter is active, while the uncited-paragraph reproduction establishes its limit.

Social discovery remains partial. The separate social monitor is not an input to this news pipeline. Available fallback search results do not establish complete exchange/social coverage or verification of the original post. Report completion and URL provenance do not establish predictive accuracy.

## Runtime and feature checks

The trading writer, analyzer, PostgreSQL and both market-data containers were healthy. The inspected containers had zero restarts and no OOM kills. Writer and automation schedulers were polling, database requests were not waiting, three private account streams were connected and the change feed was connected. Both Binance books were synchronized with fresh events and no current history errors. They had 152 BTC and 147 ETH reconnects since 15 October, indicating recovered interruptions rather than uninterrupted service.

Host disk usage was 43%, available memory approximately 7.9 GiB and swap usage approximately 3 MiB. Resource exhaustion was not demonstrated.

The signed-in production browser loaded Portfolio, Automation, History, My P&L and Builder. BTC and ETH market views both loaded current candles, spot streaming, depth and Delta context. Automation displayed the saved recheck, chart inputs and upcoming BTC reviews. History and P&L loaded historical results. No browser error/warning was recorded during those checks. These were read-only smoke checks, not every control, account role, mobile viewport, order path or accounting reconciliation.

Local backend/news tests passed 862 tests with 129 database-dependent tests skipped. Market-data tests passed 39 tests. Frontend tests passed 65 tests. Python application Ruff checks and targeted frontend ESLint checks passed. The root lint command failed because its search includes third-party Matplotlib JavaScript inside `.venv`; those are not application-source lint failures.

The first frontend typecheck found a timer-type error in `app/components/ui.tsx`. Concurrent uncommitted UI edits appeared while this audit was running. The final typecheck passed, and the updated frontend suite passed 70 tests across twelve files. The initial error describes an intermediate working-tree snapshot, not a verified production build failure. Those unrelated edits were not changed or committed by this audit.

A separate Linux run used a disposable PostgreSQL container, private test network, dummy settings, and application sources mounted read-only, without production credentials or data. It applied all thirteen migrations and completed with 987 passed and four failed, with no skipped tests. The failures require the following distinctions:

- `test_four_quarter_allocations_skip_analysis_and_release_restores_capacity` creates scheduled strategy records without `exit_at`; admission reads that field directly and raises `KeyError`. The production rows currently in nonterminal states all contain `exit_at`. This verifies a missing-field failure path, not an observed production capacity outage from that field.
- Two reporting API tests use an `Engine` test double without the newer `managed_wallet` method. The resulting `AttributeError` is fixture drift; production uses `TradingEngine`, which has that method.
- The real Agno async-dispatch test omitted a dummy `OPENROUTER_API_KEY` in the isolated environment. Its provider invocation is mocked. Rerunning that exact test with an explicitly fake key passed without a model request. Local passing tests alone had hidden this harness dependency.

These failures were recorded rather than patched as part of an audit. The disposable test containers and network were removed, and a subsequent container listing confirmed none remained. The first isolated attempt also exposed missing trading dependencies in the analyzer image used as the test base; the full run installed the declared development requirements in its disposable runner. That image separation is expected and does not establish missing dependencies in the production trading writer.

## Recommended order

Fix the expired-contract recovery loop and expose unresolved recovery/risk failures in operational health. Refresh cached market evidence before selection, preserve original recheck failure details and make provider allowance actionable. Correct the committed-action report and stale proposal lifecycle. Make candidate selection evaluate real contract geometry, add numerical/unit consistency checks, and test the new model on fixed historical evidence before deciding whether to change it. Repair or replace consistently failing news connectors and close the uncited-claim provenance gap. Update the integration fixtures so the full suite can become a reliable release check.

Do not force recheck approvals to restore trading volume. The evidence supports fixing candidate quality and diagnostics; it does not establish that the dropped trades should have executed.
