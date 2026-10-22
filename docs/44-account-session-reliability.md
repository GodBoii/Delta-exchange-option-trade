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

## Implemented behavior

The workspace now models loading, signed-out, verified, and unavailable sessions
separately. Auth events invalidate in-flight checks on sign-out or a different
user, and private feature views remount for a different authenticated user.
Temporary failures retain only the same user's last known connection. The UI
offers public/offline capabilities until verification recovers, with a 15-second
retry and checks when connectivity or foreground visibility returns.

Normal sign-out uses Supabase's local scope. API errors retain HTTP status and
backend codes. A rejected authenticated GET can refresh and retry once, with
coalescing and a cooldown to prevent refresh loops. Writes are never replayed.
Delta credential errors do not terminate application authentication. Older
backends returning an unauthenticated HTTP 200 are handled during rollout.

The session endpoint requires application authentication. Revoked connections no
longer become connected after reload. Fixed-name session events and credential
connection/revocation logs use verified user/session identifiers without tokens,
secrets, emails, request bodies, or exchange payloads.

The portfolio headline selects USD explicitly and preserves a reported zero USD
row. Other funded assets remain separate. Missing or invalid wallet data does not
become a fabricated zero, and non-USD margin is not added to USD margin.

Theme preferences use timestamped localStorage and a non-sensitive cookie. The
newest successfully saved preference wins, including when either store fails.
Legacy plain-text preferences remain readable. The before-paint script and React
use the same selection rules. When both stores fail, the account panel explains
that the theme applies until reload. Normal localStorage changes synchronize
across tabs; a cookie-only fallback is picked up by other tabs on reload.

Main-account key setup now verifies the profile and wallet, position, and
order-list responses before saving. Probe failure preserves the previous key.
The interface says that read access was checked and does not claim that a read
probe establishes order-placement permission. No test order is submitted.

## Verification

The production build, TypeScript checking, and ESLint passed. The frontend suite
passed 97 tests. Regression tests cover typed errors, read-only auth
recovery, no write replay, refresh coalescing/cooldown, account identity changes,
outage retention, revoked/disconnected sessions, device-local sign-out, USD wallet
selection, legacy theme values, partial storage failure, and before-paint restore.

The backend suite passed in the repository virtual environment: 895 passed and
138 skipped. Skips require unavailable isolated database or other integration
fixtures. The system Python was missing unrelated test dependencies; it was not
used for the final full suite. Production databases were not used for tests.

Browser checks used the real Home, AppShell, Dashboard, Automation, and theme
components with temporary auth and network fixtures. Checks covered an outage
and recovery without losing the account, a response arriving after sign-out, an
old response arriving after a different user signs in, device sign-out, a zero
USD wallet alongside REF_USD, Dark surviving reload after a localStorage write
failure, and visible feedback when both stores fail. The owner had three
automation switches and the regular user had one, preserving the global-control
boundary. The 390px phone viewport had no document horizontal overflow.

Temporary verification routes and client substitutions were removed before the
final build and commits. Screenshots contain fixture accounts and are stored
outside the repository. No real sign-out, order, transfer, capital edit, or
participation edit was performed during browser verification.

## Backend release

The backend was released from `main` at `1de5c3f` on 22 October 2026. Before
recreation there were no active strategies, entries, exits, analyses, or rechecks.
The rebuilt trading API became healthy. Post-release checks confirmed healthy
schedulers, profile synchronization and database connections. Both absent and
invalid application tokens returned HTTP 401 with `not_authenticated`.

Read-only database checks confirmed that all saved Delta connection statuses and
credential-update timestamps were unchanged. Child participation remained
disabled, and no account-management overrides were introduced. The frontend
release follows the main-branch push after final validation.
