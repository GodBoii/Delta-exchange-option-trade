# Standalone Reddit research monitor

This is local research infrastructure with a keyless RSS adapter. It needs Python
3.11 or newer and has no runtime dependencies. Everything it authors or stores is
inside `reddit_monitor`. It does not import the trading application or X monitor,
send notifications, use LLMs, infer trades, or submit orders.

Live access is disabled. No Reddit collection or deployment has been verified.
See [access research](ACCESS_RESEARCH.md) for the current approval requirements,
keyless repository comparison, and RSS limitations.

## Implementation plan and result

1. Inspect X evidence and export without editing it. X exports its own post model
   with numeric IDs and `first_seen_at`. Reddit preserves `t1_` and `t3_` IDs.
2. Build separate SQLite evidence, page checkpoints, screening and export.
3. Add bounded replay and a keyless RSS adapter that requires research-route approval.
4. Verify recovery, deduplication, baseline suppression, timestamps, failures,
   locking, erasure and quota handling through real SQLite and synthetic responses.

The code and tests implement those steps. Approval, live RSS field availability,
feed freshness and a permitted deletion-refresh procedure still need verification.

## Run offline

Run from the repository directory in PowerShell. These commands create only
ignored local state inside `reddit_monitor/state/offline`.

```powershell
python -m reddit_monitor validate
python -m reddit_monitor replay reddit_monitor/examples/baseline.jsonl --subreddit solana --at 2026-10-20T10:00:00Z
python -m reddit_monitor status
python -m reddit_monitor export
python -m unittest discover -s reddit_monitor/tests -v
```

The fixture contains three synthetic posts across two pages, one relevant, one
unrelated and one uncertain. Running it again preserves three evidence revisions
and emits no research events. It never contacts Reddit. Fixture publication times
are earlier than receipt times. Old replay receipt times do not establish live health.

Replay tapes contain one JSON object per response, with `subreddit`, `stream`,
`cursor_in`, `after`, `received_at`, and `children` in Reddit Listing child shape.
Instead of children a record may contain an allowlisted `error` code and optional
`cooldown_until` epoch seconds. Separate tapes can model recovery after a partial
read. The next tape must start at the persisted cursor.

Offline state and approved live state cannot share a database. `claim_mode`
persists the access mode and refuses later changes. Make a different local config
and state directory for approved live work. Configuration paths cannot point
outside `reddit_monitor`.

## Collection behavior

The collector checks new posts and recent comments separately for each community.
Defaults bound each attempt to four pages of up to 50 items, with two seconds
between requests and 120 seconds between rounds. The roster contains four
communities, so a full one-page round would request eight feeds. Actual delays
include response duration and retries. No RSS rate entitlement is assumed.

Each saved page atomically commits useful evidence, its receipt time, input/output
cursor and provenance. Failed subsequent pages leave earlier evidence and cursor
intact. Restart resumes a pending scan. An initial scan remains a baseline across
partial reads and restarts, until it reaches the end of the returned window.
Baseline evidence never becomes a new research event.

Later discoveries receive a `research_event` flag only if first seen after baseline,
published after baseline start, no older than one hour, and carrying an explicit
publication timestamp. Synthetic replay always sets this flag false. The flag is
stored evidence metadata, with no notification delivery mechanism. Revisions of
known IDs and stale backfills do not become new research events.

Raw variants and initial receipt timestamps persist. Repeated identical variants
update `last_seen_at` without duplicating evidence. A small persistent native-ID
index survives content retention expiry, so old content is not rediscovered as new.
This index contains no author names, text or IDs.

RSS is a bounded recent window. Only an explicit same-endpoint Atom `rel=next`
link permits pagination. The collector does not invent cursors or assume a missing
next link proves historical completeness. `window_unverified`, `empty_unverified`,
`page_limit`, `partial_*` and `failed_*` states remain distinct. Cyclic continuation
within an attempt fails without advancing the cursor. Invalid items retain useful
siblings and retry that page on the next attempt.

HTTP transport failures retry at most three times with spacing and backoff.
HTTP 429 persists a global cooldown across targets and restarts, honoring numeric
or date `Retry-After`. Access denial stops the process. Redirects are refused;
there is no browser impersonation, proxy rotation, cookie import, account rotation
or anonymous JSON fallback. Diagnostics expose fixed error codes, never response
bodies, arbitrary exception messages or authentication headers.

`run --once` returns 2 for incomplete, failed, empty or cooldown results. A successful
bounded read can return 0 while coverage remains unverified. `status --check`
requires every stored target's latest attempt to be a recent useful read. It does
not certify collection completeness, Reddit access approval, or a profitable signal.
An OS lock permits one writer per state directory and releases after process exit.

## Screening and controls

All valid evidence survives screening. Keyword matches label crypto relevance.
Ambiguous meme terms, cashtags and EVM address strings without explicit crypto
terms become `uncertain`. Other content becomes `unrelated`. These are controls
for later human review, not discarded noise. The screening version is stored with
each revision. It does not score market direction, resolve assets or verify contracts.
RSS HTML metadata may affect keyword matches. Images and meme meaning are not interpreted.

## Retention and deletion

Approved live mode purges content older than 48 hours during collection and filters
expired content from exports. Status and export use read-only database connections
and can run while the collector holds its writer lock. Export reads short batches;
it is a best-effort view of retained data rather than a fixed-time database snapshot.
The setting can shorten this period. Offline synthetic fixtures remain available
until explicitly purged. A stopped process cannot perform timed erasure; run
`python -m reddit_monitor purge` to enforce expiry while collection is stopped.

Structured `[deleted]`, `[removed]` or removal-category observations erase prior
variants and keep a content-free tombstone. Deleted-account observations erase
previously related author observations. Manual erasure uses:

```powershell
python -m reddit_monitor erase t3_example
```

Erasure blocks later resurrection of that ID by stale responses. Ordinary edits
retain earlier versions within the allowed retention window. Reddit deletion
requirements override historical preservation. SQLite secure-delete is enabled;
the database does not use WAL. Protect the state directory using local filesystem
permissions. Filesystem snapshots, backups and previously exported copies need
separate erasure. Do not commit evidence exports or copy live data into fixtures.

RSS does not reliably expose deletions or account removal. Disappearance from a
feed is not proof of deletion. The 48-hour expiry limits retention but cannot prove
immediate deletion compliance. Before any live deployment, agree on a permitted
refresh/removal method with Reddit and verify it. There is no deployed service,
background task or Compose project to accidentally start with the trading system.

The [export contract](EXPORT_FORMAT.md) describes future source-preserving combination.
