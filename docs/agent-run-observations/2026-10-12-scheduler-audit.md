# Backend and scheduler audit, 12 October 2026

The shared review admission path had a reproducible legacy-settings bug. It is fixed in production. The 19:00 IST BTC review started at 19:02:02 after the fix. This audit does not certify that every future run will succeed through exchange, model-provider, network, or host outages.

## Confirmed blocker

`has_automation_trade_capacity` requests `user_id,asset_enabled` from the enabled accounts. Older account settings have no `asset_enabled` field. The PostgreSQL compatibility projection returns that field as `None`, rather than omitting it.

`automation_asset_enabled` previously called `.get` on that null value. The shared filter raised `AttributeError: 'NoneType' object has no attribute 'get'` before checking any wallet. Both assets were deferred until the ten-minute admission window expired.

The defect was observed in the live 19:00 review logs. Today's 12:30 and 15:30 BTC and ETH records also show expiration without starting, but their original backend logs were lost when that container was replaced. The live reproduction explains the same failure path; those earlier per-attempt exceptions cannot be independently recovered.

The initial live preflight called account capacity directly and did not exercise the projected shared-account filter. That was insufficient. The new PostgreSQL regression uses legacy settings without the field, the actual paginated settings projection, and the shared admission function for BTC and ETH.

The fix treats null asset flags like missing legacy flags. Explicitly paused assets still stay paused. Account capital and asset-allocation policies are unchanged.

## Scheduling and diagnostic changes

- Deferred jobs retain `last_deferred_reason` and `last_deferred_at`. Expiration records the prior blocker in `expiry_reason` instead of losing all diagnostic context.
- Analyzer readiness deferrals now record their own reason.
- Fixed-review catch-up covers the full ten-minute admission window. A restart seven minutes after a session can recreate a missing review while it is still eligible.
- Loopback health probes expose agent scheduler liveness, recent successful polling, errors, and active main/recheck counts. Public health responses retain only the service identifier. Compose health checks now include the agent scheduler.
- Backend logs rotate in the existing persistent trading-state volume and retain files for thirty days. Configured database URLs and service secrets are redacted. Logging configuration is idempotent.
- The offline agent-input diagnostic no longer requires provider credentials. Model execution remains replaced by its recording agent.

## Code and architecture checks

The review traced session generation, due-job selection, shared/account admission, row-locked claims, analyzer readiness, main and recheck worker pools, result persistence, shared allocation, wallet-scoped capital reservations, execution recovery, credential storage, and local health probes.

Existing safeguards remain in place: one trading writer per shared state volume, transactional job claims, wallet-scoped reservation locks, account and asset pause checks, saved-library version checks, activation rechecks, and entry recovery before new orders. Analysis POSTs are not blindly retried after ambiguous disconnections. The analysis database role is separated from trading tables, and exchange credentials remain encrypted on the backend.

The architecture deliberately gates research on capacity. With full-balance allocation, an in-progress BTC decision can make ETH wait. A funded BTC position can leave no room for ETH. The audit did not disable this rule or increase the capital allocation.

Health checks expose failure; Docker's `unless-stopped` policy alone does not restart a process solely because its health check becomes unhealthy. Persisted diagnostics make that condition inspectable, but they are not an external alerting service.

## Verification

- 732 backend tests passed on Linux using disposable PostgreSQL, including the separate ETH catalog database. No tests were skipped in that run.
- The exact legacy-settings shared-admission regression passed for both assets. Focused scheduler/readiness/admission verification passed 40 tests.
- Ruff passed for backend application code and changed scripts/tests. Whitespace checks passed.
- Test failures found during the broader run were corrected: an incomplete library fixture, a hard-coded test database name, the offline diagnostic's key requirement, and an overly short process-import allowance in the provider-error propagation test. Production analysis timeouts were not increased.
- Deployments occurred with no running analysis or live/entering trade. The second deployment occurred while the affected reviews were still queued. The analyzer and market-data services were not restarted.
- Production health verified both schedulers running without polling errors, the analyzer ready, successful wallet reads, and a writable retained-log directory.
- The disposable database container was removed after verification. Private local credential artifacts were kept out of commits.

## Live review and external limits

The repaired BTC review started at 19:02:02 IST. News research completed at approximately 19:07:14, and strategy analysis followed. ETH was waiting on `analysis_capacity_pending` under the existing full-balance policy, rather than failing on the null-settings exception.

The credential-safe provider metadata check reported a USD 25 key limit, USD 22.8648 usage, approximately USD 2.1352 remaining, and no automatic reset. Funding remains a constraint on future runs. Analyzer readiness does not prove remaining provider credit or guarantee exchange/network availability. The live model request, rather than health alone, established that this review could reach the provider.
