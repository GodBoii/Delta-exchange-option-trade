# Crypto monitoring, version 0.5

The standalone Compose project uses `config.crypto.toml`. It watches twenty-four
accounts selected on 15 and 20 October 2026 and emits alerts only
for posts with explicit crypto evidence. The old `config.example.toml` and existing
local configurations retain their prior behavior. Following an account on X does
not change either configuration.

The cadence is 60 seconds for CZ, Vitalik, Lookonchain and Wu Blockchain, 180
seconds for the other accounts, with 300 seconds for Ansem and Billy Markus.
The twelve added accounts use 90-, 120- or 300-second intervals. All sources use
the reply-inclusive endpoint, which had a 500-request quota during the live pilot;
the original-only endpoint had just 50. Reply IDs distinguish that context from
original announcements. [ROSTER.md](ROSTER.md) records the identities, selection evidence, source
conflicts and forecast-evaluation limits.
Twenty-four accounts share the existing session. Quotas, backoff and
request duration can increase those intervals. This is a research collector, not
a low-latency feed. Measure receipt delays before changing cadence.

## Relevance and retained evidence

Every new post is retained, including unrelated and uncertain posts. A separate
`assessments` table records the screening version, named assets, evidence from the
original/quoted/reposted text, event categories, unresolved cashtags and eligibility
for prospective research. Replies without available context and image-only posts
remain uncertain. Unknown cashtags alone are uncertain because stock and crypto
tickers overlap. URLs and author handles alone do not establish relevance. The
filter is deterministic text screening; it does not interpret images, resolve
missing reply context or promise complete semantic coverage. Crypto-related
promotions may pass and should not be interpreted as recommendations.

Screening version 2 recognizes custody terms, selected protocols, stablecoin
names, tokenized assets and activity at a small explicit venue-handle list. Generic
wallet theft, school-network hacks and language-model tokenization remain outside
those rules. New posts receive the new version; stored assessments and research
snapshots retain their original version. `review` compares them without rewriting
history. Poll relevance counts describe newly stored posts rather than every
repeated timeline result.
Version 3 adds explicit on-chain security terminology and selected meme project
names, while retaining unresolved tickers and unverified token identity. Pinned
new accounts must match their configured numeric author ID before timeline access.
An identity mismatch fails health for that source and leaves other sources running.

Post direction stays `unclear`. A deposit is not proof of selling, and a mention
is not an endorsement. Named assets and addresses are candidates, never verified
token identities. User IDs remain stored with each post; handle ownership is not
pinned in the roster. Review account identity if a handle changes ownership.

## Price-response worker

The separate `research` service reads monitor evidence through a read-only SQLite
connection and writes `state/research/market.sqlite`. Its lock is separate from
the collector's lock, so slow market requests cannot block X collection. It uses
only Binance's public spot candle endpoint, with no exchange keys or orders.

Fresh posts collected after a target's baseline create jobs at 1, 5, 15 and 60
minutes. Unrelated and uncertain posts are included as controls. Baseline history,
stale posts and pre-upgrade evidence are not silently turned into new events.
Jobs persist across restarts. Missing data and HTTP failures retry with bounded
backoff, become explicit failed jobs after eight attempts, and are never converted
to zero returns. A rate-limit response delays all pending market jobs.

Each result includes:

- Five-minute pre-publication movement, publication-to-receipt movement, and returns
  measured separately from publication and receipt.
- BTC and ETH benchmarks for every post. Named BTC, ETH, SOL, BNB, DOGE, SHIB, PEPE,
  WIF, BONK and FLOKI mentions also get their configured USDT spot price proxy.
- Up/down/flat observed price direction and simple benchmark return differences.
  These differences are not beta-adjusted abnormal returns or causal estimates.
- Actual candle close timestamps, close/volume observations, errors and unresolved
  references. Unlisted tokens and contract-only mentions have no invented price.

All anchors use the last completed one-minute candle before the requested time,
so an anchor can be almost a minute old. Publication and receipt may use the same
candle. Sub-minute reaction speed cannot be inferred. Results arrive after their
horizon and worker scheduling delay. Candles are fetched retrospectively and are
not evidence of quotes available to the monitor at decision time. Returns exclude
fees, spreads and slippage. No executable profit or causality is claimed.

The ten named markets are price proxies. Ticker text does not verify a meme coin's
chain/contract, and unrelated tokens can reuse those symbols. Contract resolution,
DEX liquidity, executable quotes and automatic trading are not implemented.

## Commands

From the repository root, run collection and research in separate terminals:

```powershell
social_monitor/.venv/Scripts/python.exe -m social_monitor --config social_monitor/config.crypto.toml validate
social_monitor/.venv/Scripts/python.exe -m social_monitor --config social_monitor/config.crypto.toml run
social_monitor/.venv/Scripts/python.exe -m social_monitor --config social_monitor/config.crypto.toml research
social_monitor/.venv/Scripts/python.exe -m social_monitor --config social_monitor/config.crypto.toml research-status --check
social_monitor/.venv/Scripts/python.exe -m social_monitor --config social_monitor/config.crypto.toml research-export
social_monitor/.venv/Scripts/python.exe -m social_monitor --config social_monitor/config.crypto.toml coverage --hours 24
social_monitor/.venv/Scripts/python.exe -m social_monitor --config social_monitor/config.crypto.toml review --hours 168 --label uncertain
```

`research --once` discovers a bounded batch and processes up to eight due jobs.
Future jobs remain queued. An attempted incomplete measurement returns exit 2.
Research JSON on stdout is a convenience stream; the database/export is the
durable record. Collector stdout retains its existing durable outbox semantics.
Worker health checks heartbeat freshness, overdue jobs and terminal failures;
check collector health separately to determine whether new posts are arriving.
Docker uses a lightweight probe with the same operational health rules as the CLI.
Collector `healthy` requires a recent successful read for every configured target;
`degraded` preserves any latest failed/partial read. Both can be true after one
transient failure. Neither field verifies timeline completeness.

Data retention remains manual. Research results repeat candle observations across
horizons for auditability, so monitor volume growth. There is no automatic replay
of failed jobs or deletion of existing evidence.

Source: [Binance public market data](https://developers.binance.com/en/docs/products/spot/faqs/market_data_only)
and [candle endpoint](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market).
