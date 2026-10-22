# Account session reliability

Implementation plan prepared on 22 October 2026 after reviewing the application
session loader, Supabase browser/server clients, middleware, API helper, backend
authentication and credential storage, portfolio display, theme provider, existing
tests, and the storage and subaccount documentation.

## Scope

Repair application-session handling, device-local sign-out, revoked-connection
reporting, USD wallet selection, theme persistence, main-key read-access checks,
and safe authentication diagnostics.

BTC and ETH switches remain owner-only. Capital allocation, caps, eligibility,
subaccount participation, funding, assignments, scheduling, execution, and risk
monitoring are outside this change. Existing credentials and account settings
must not be migrated or reset.

## Implementation sequence

1. Correct backend authentication and connection responses. An invalid application
   session must not look like a missing Delta connection. A revoked connection
   must remain disconnected on reload. Add tests and safe logs without secrets.
2. Reconcile frontend authentication events with the main workspace. Clear private
   views on sign-out or a change of user, reject stale asynchronous responses,
   preserve connection information through temporary outages, and retry recovery.
   Make normal sign-out local to this device and handle SDK errors.
3. Preserve API status and error codes. Retry a read once after a recoverable
   authentication failure. Never automatically repeat a write or treat a Delta
   credential error as an application logout. Support the older session response
   during the backend/frontend rollout.
4. Select the USD trading wallet explicitly, including a reported zero USD row.
   Keep other assets visible separately and never add their units to USD margin.
   Do not synthesize a zero balance when wallet data is unavailable.
5. Save theme preference with a browser-cookie fallback when localStorage fails.
   Read the same preference before paint and after hydration. Report when neither
   store can retain the choice, and verify reload and cross-tab behavior.
6. Before accepting a main Delta key, check profile identity and required wallet,
   position, and order-list read access. Make no trade to test permission, and
   describe the verified access accurately in the connection screen.
7. Run focused and broader regression checks, type checking, lint, production
   build, and browser checks with temporary fixtures. Remove fixtures before
   committing. Record results and rollout requirements here.

## Safety checks

- A response for an old user cannot repopulate a signed-out or different user's
  workspace. Private views must remount when the authenticated user changes.
- Authentication recovery must not replay order, transfer, strategy-creation,
  credential, or configuration mutations.
- A service outage must not delete stored Delta credentials or claim that the
  connection was revoked. Explicit disconnection must not sign out Google.
- Sign-out on this device must not revoke another device's refresh token.
- Wallet presentation must not alter backend sizing or capital policy.
- New connection probes must finish before credentials are persisted. Failure
  must preserve the prior connection.
- Logs must omit credentials, tokens, emails, exchange response bodies, and
  request bodies. Use verified user/session identifiers and fixed event codes.
- Tests requiring PostgreSQL must use an isolated test database, never production.

Work stays on `main`. Each changed file is committed separately and commits are
pushed to `origin main` as development progresses. Production backend changes
require a backend-first release; frontend code must remain compatible while the
two deployments differ.
