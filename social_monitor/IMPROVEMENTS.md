# Research monitor improvements, 19 October 2026

The work stays inside the standalone research monitor. The existing trading
application, agents and user notifications are outside this change.

## Implementation sequence

1. Expand screening around observed misses: custody incidents, Qubic, stablecoins,
   tokenized assets and financial activity at explicitly recognized venue handles.
   Add unrelated controls for ordinary wallets, school networks, language-model
   tokenization and ambiguous tickers. Preserve unresolved token identities.
2. Keep useful partial pagination results on the normal front-page schedule.
   Retry transient empty responses twice at 30 and 60 seconds before exponential
   backoff. Reported quota cooldowns always take priority. Preserve received pages
   when a later page times out. Authentication and access stops remain immediate.
   Handle Docker SIGTERM through async cleanup so ordinary stops release the
   upstream endpoint lease instead of leaving a 15-minute lock until expiry.
3. Stop rescreening known posts and avoid grouping all historical polls for status.
   Add an index for the last successful attempt. Use a small database-only Docker
   probe that does not import the event loop, collector or market client.
4. Add local `coverage` and `review` commands. Compare one page from X's original
   posts endpoint with stored IDs through `verify`. This is an alternate endpoint,
   not an independent source or proof of exhaustive coverage.
5. Poll CZ, Vitalik, Lookonchain and Wu Blockchain every 60 seconds. Retain the
   other intervals. Estimate 114 timeline requests per 15 minutes including
   reconciliation, before retries and lookups, against the observed 500 quota.
   Actual quota response headers remain authoritative.
6. Validate on Windows and Linux, back up both evidence databases, verify migration
   on a copy and deploy only the standalone services. Compare stored counts,
   assessment payloads and research results across the change. Keep the old release
   and image available for rollback.

## Evidence rules

New posts use collector version 4 and screening version 2. Poll relevance counts
now describe newly stored posts, with `assessment_scope="new_posts"`. Every raw
post remains retained. Known post IDs, pending outbox entries and study jobs retain
their existing meaning.

Historical screening payloads and market-event snapshots are immutable in this
rollout. `review` computes a current assessment beside the original without
rewriting either database or emitting historical alerts. It does not create
retrospective study events or assign prices to unresolved tickers.

The initial offline comparison covered 893 saved non-crypto posts. Screening
changed 22 to crypto, including six prospective events. The confirmed Qubic,
Ledger, AGT and tokenized-stock misses were recovered. This targeted comparison
is not an unbiased precision or recall estimate.

## Health meaning

Collector health requires a recent successful poll and recent attempt for every
configured target. The freshness budget is three configured intervals plus one
request timeout. A single failed attempt can therefore report `healthy=true`
and `degraded=true`; error codes remain visible. Sustained failure, unpolled
targets, future timestamps and blocked or invalid authentication fail the probe.
An old publication time never silently becomes proof of a missed post.

The research probe retains its heartbeat, overdue-work and terminal-failure
checks. Both probes are read-only and bounded by a short SQLite lock timeout.
The Docker five-second timeout is unchanged so the implementation must become
cheaper rather than receive a looser timeout.
The probe reads only its required TOML fields and avoids importing configuration
dataclasses, the collector, the event loop and the HTTP client dependency tree.
The image precompiles Python bytecode at build time. The slim base had no cached
bytecode for the probe's JSON, TOML and regular-expression modules; a read-only
container otherwise recompiles these modules on every probe.

Both services retain restart policy `no`. This preserves intentional access stops;
automatic recovery after a host reboot is not part of this research update.

## Verification and rollout

Run the unittest suite and Ruff checks from the README. Tests cover the observed
screening failures, partial and transient scheduling, timeout evidence retention,
history preservation, read-only review and verification, and probe failure states.
Deployment measurements are recorded in OPERATIONS.md after live verification.

Faster configured polling is not a measured publication-to-receipt improvement.
New-post samples are required after rollout. Upstream empty responses and missing
reply context remain observable limitations, not solved platform guarantees.
