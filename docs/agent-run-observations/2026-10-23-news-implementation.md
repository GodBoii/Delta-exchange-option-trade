# News implementation, first stage

Implemented on main, without deploying application changes to production.

## Changes

- Main strategy analysis collects market and option evidence after news completes, before rendering charts.
- Public-source follow-up search and direct agent search share an instance, cache and budget.
- Discovery leaves the final 45 seconds of the default budget for original reads and synthesis.
- Topic arguments add a bounded discovery query while retaining baseline sources.
- Four verified public feeds expand coverage to Bank of England news and Coinbase, Kraken and Circle status.
- Source-health results distinguish future-only notices from current material.
- Targeted HTML passage reads reuse up to 128,000 retained characters without downloading again.
- Uncited quantified report claims are omitted; partial source coverage is persisted and disclosed.

These changes preserve Markdown output, existing tool names, session storage and action interfaces.
The numerical guard checks citation presence, not whether a passage semantically supports every claim.
PDF extraction retains its existing limits. No schema migration is required.

## Verification

- Baseline news suite: 65 passed.
- Local full backend suite: 909 passed, 139 database-dependent tests skipped.
- Linux full suite against a disposable PostgreSQL 17 container: 1,038 passed, ten ETH catalog tests skipped.
- Separate disposable ETH catalog database: all ten skipped catalog tests passed.
- After the final future-notice correction: all 73 news tests passed on Windows and Linux.
- Ruff checks passed for news_agent and automation_agent/team.py.
- Live collection checks used public-source retrieval from ubuntu-server, without model calls or trading actions.

The Linux runner used a separately built test image, an isolated Docker network, disposable databases,
dummy credentials and a read-only source mount. Production credentials and production data were not used.
The full database run preceded the final future-notice correction; the news suite was rerun afterward.
Tests exercise mocked provider dispatch. A real paid model run and production deployment remain unverified.

## Live connector findings

CFTC was readable on 23 October, but the newest release was outside the 48-hour collection window.
Binance, both GDELT connectors and Polymarket still timed out. Blockworks returned an old feed.
These results do not justify describing an old official feed as a broken connector.
Bank of England and the three added status feeds returned parseable entries from the server.
Some status-feed dates describe future maintenance and must not be treated as current outages.

## Remaining agreed implementation

- Repair or replace the still failing connectors after validating their documented interfaces.
- Passage-level semantic and numerical claim validation; structured claims/events alongside Markdown.
- Persistent event memory, corrections/retractions and story clustering with source-independence checks.
- Background collection and shared macro evidence beyond the existing short-lived source cache.
- Integrate social/Reddit collectors and expand institutional, regional, calendar and language coverage.
- Add structured release comparisons with matching units, reference periods and revision handling.

Breaking-news triggers, pre-activation news refresh, operational monitoring, historical evaluation and
reproducible evidence archives remain outside the current scope at the user's request.
