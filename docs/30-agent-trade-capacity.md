# Agent trade capacity, October 2, 2026

Agent reviews now check capital capacity before making an analysis request. Previously, only entry reservation enforced this limit, so a full account could still pay for an agent review.

The check reuses the existing capital policy and entry budget calculations. Percentage settings allow one, two, three or four occupied strategy slots for 100%, 50%, one third or 25% respectively. BTC and ETH share these slots. Fixed-dollar settings keep the existing wallet-based slot limit and 100-slot ceiling. Reserved and active slots count; released slots do not. Budgets reserved by other logins connected to the same Delta account count against that wallet too.

Shared analysis runs if any enabled, connected account has capacity. An unavailable wallet does not prevent a run for another account with confirmed capacity. If no account has confirmed capacity and a lookup failed, scheduled reviews stay pending for retry under the existing ten-minute lateness limit.

Scheduled sessions, manual runs queued for shared analysis, agent follow-ups and activation rechecks are cancelled before claim when capacity is exhausted. Their history records `No trade slots are available` and a skip report. Direct manual requests return HTTP 409 with `capital_slots_full` before creating a run. Execution checks capacity again after claim to cover changes while a run was queued.

The check reads capacity without reserving it. Entry retains its atomic reservation, fresh wallet sizing, account locks and exposure checks. Concurrent analyses can still start while capacity exists. Active-trade monitoring, exit handling, reconciliation and capital release are unchanged. Private wallet data stays outside the shared model context.

## Verification

- Focused capacity, scheduling, readiness, timeout and shared-analysis tests: 100 passed.
- Disposable Linux/PostgreSQL suite: 499 passed, four failed. The original `a5246f7` commit produced the same four failures with 448 passing tests in the same environment. This change adds 51 passing tests and no new failures.
- Existing failures: `test_local_api.py::test_local_library_and_private_research_endpoint`, `test_local_application_data.py::test_library_save_revision_and_soft_delete`, and the shared-publication and account-scheduling tests in `test_local_research_operations.py`.
- Real PostgreSQL tests cover mixed BTC/ETH slots, linked-account budgets, read-only capacity queries, skip-report persistence, fifth-entry rejection and capacity returning after a completed trade releases its slot.
- Ruff passed for every changed Python file. No database migration was required.

## Deployment

On `ubuntu-server`, `/home/arun/apps/delta-exchange` pulled `main` with `git pull --ff-only origin main`. The `delta-exchange` image was rebuilt, and the backend was recreated with `docker compose up -d --no-deps --force-recreate delta-exchange`.

The backend container is healthy. Local health checks confirm the writer scheduler is running, polling completes without errors, and the database change feed is connected. The public API health endpoint responds successfully. SHA-256 checks confirm the four changed backend modules match the server checkout. A read-only production capacity check found one enabled, connected account with no occupied slots and confirmed shared analysis has capacity. No funded orders were used for verification.
