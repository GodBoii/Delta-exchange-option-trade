# Verification, 20 October 2026

Verified locally on Windows with Python 3.13. No live Reddit collection was run.

- `python -m unittest discover -s reddit_monitor/tests -v`: 20 tests passed.
- Ruff lint and formatter checks passed for all Reddit Python files.
- `git diff --check -- reddit_monitor` passed.
- CLI validation passed using the default offline configuration.
- A fresh isolated CLI replay, followed by the same replay again, exported exactly
  three synthetic revisions with relevant, uncertain and unrelated labels.
- Every fixture export had `research_event=false`.
- `status --check` returned 2 for offline state and listed missing roster targets.
- `run --once` returned 2 with `research_route_approval_required`, before network
  access. The keyless adapter has no API-key or cookie handling.
- Read-only CLI export passed after checking that status/export readers can read
  while the collector's OS writer lock is held.

The behavior tests use real SQLite files. They cover restart after a partial
baseline, cursor recovery, deduplication, page limits, stalled pagination, invalid
items and receipt timestamps, comments/thread IDs, timezone validation, future
timestamps, ordinary edits, deleted content, copied crosspost erasure, deleted
authors, tombstones, content expiry with persistent deduplication, state-mode
isolation, global restart-persistent quota cooldowns, HTTP 429, access denial,
bounded credential-safe transport failures, redirect refusal, process locking,
read-only reports, relevance controls and unknown RSS publication/identity fields.

The original X monitor and trading services were not stopped, rebuilt, restarted
or deployed by this work. Concurrent changes under `social_monitor` and unrelated
encrypted files were left alone. Only files under `reddit_monitor` were authored
and individually committed.

## Remaining checks before live use

1. Obtain Reddit's approval for this exact purpose and collection route. No current
   approval is established. The user requested keyless access, not OAuth setup.
2. Verify permitted feed behavior from the intended host, including native IDs,
   explicit publication times, comments, redirects, HTTP limits and pagination.
3. Establish a permitted deletion/account-removal refresh process. RSS alone
   cannot establish complete deletion detection or complete author identity.
4. Use a separate approved state directory and protect its filesystem permissions.
   Confirm access through a bounded manual run before any continuous deployment.

No Compose service, scheduled task, user notifications, LLM worker, trading
connection or order execution was added. Passing local tests proves the local
pipeline behavior under those fixtures; it proves neither live coverage nor a
profitable relationship between Reddit activity and market moves.
