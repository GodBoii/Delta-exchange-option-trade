# Owner Users redesign

## Current behavior

The Next.js Users view reads owner-only FastAPI reporting endpoints. PostgreSQL stores
account participation, capital observations and an owner ledger that retains deleted
runs. Personal P&L already has asset filtering, cumulative and gains/losses charts,
and a daily IST calendar. Owner reporting currently omits those filters and charts.
Capital history renders all 30 observations before performance.

Global BTC/ETH automation flags apply to every account. Account participation has
only one switch. Independent account asset preferences must be enforced in shared
allocation, entry authorization and capital reservation, not only displayed in UI.

## Implementation sequence

1. Persist account BTC/ETH preferences in existing automation JSON. Preserve legacy
   defaults and the master switch. Cancel only the paused account/asset's pending
   automated work transactionally; preserve active positions and manual strategies.
2. Expose asset filtering on owner summaries and paginated trades, and an owner daily
   calendar that includes deleted runs. Echo asset scope so stale APIs cannot silently
   return combined results beneath a BTC/ETH label.
3. Reuse existing charts through explicit owner endpoint props. Keep personal reports
   on their existing endpoints and retain the owner archive inspection flow.
4. Arrange Users as a compact account rail and selected-account workspace. Use a
   status strip, short master/BTC/ETH switches, then shared asset/period controls,
   performance, cumulative and gains/losses charts, daily calendar, and trades.
5. Put capital controls and observations in an expandable section. Present six
   observations per page with wallet/policy/allocation filters and a timeline spine.
   Wallet capital remains account-wide even when performance is asset-filtered.
6. Verify backend isolation, cancellation and legacy behavior, frontend reporting,
   production build, and browser desktop/mobile/theme/keyboard/error states. Commit
   and push focused stages on main.

## Design decisions

This is an owner operations dashboard. Account selection and automation state come
first; audit details stay available without dominating the page. Reuse the existing
dark/light tokens, gold selection color, green/red financial semantics, display face,
and tabular numeric type. Spend visual novelty on connected asset controls and the
capital timeline. Native buttons, radios and disclosure controls remain keyboard
accessible. No new animation or chart dependency is needed.

## Safety facts

- An account/asset pause must not change another account, the other asset, global
  analysis, an active position, or a manually scheduled strategy.
- Owner charts, summaries and tables must use the same asset and include deleted
  ledger entries. Personal reporting must continue to exclude deleted entries.
- Master switch changes must preserve saved asset preferences.

## Verification results

- 78 backend tests passed against an isolated PostgreSQL 17 database, covering
  owner authorization, mixed-asset reporting, archive inclusion, asset-specific
  cancellation, stale claims, future sessions, allocation and entry checks.
- 40 frontend tests passed. TypeScript, focused ESLint and Ruff passed.
- The production Next.js build passed. The shared chart components retain their
  personal-report endpoint defaults and now support explicit owner endpoints.
- Local fixture browser checks covered 1440px desktop and 390px mobile, dark and
  light themes, account selection, keyboard switches, failed saves, reload,
  All/BTC/ETH chart changes, search with no matches and capital-history filters
  and pagination. Both viewports had no horizontal page overflow. Charts and
  calendar cells rendered; browser logs contained no errors or warnings.
- Mobile accounts use a horizontal ribbon, profile details use two columns, and
  each asset control is about 111px wide. Desktop controls are about 145px wide.
- Existing runtime fixtures now explicitly enable account participation. Two
  research fixtures failed on the unchanged baseline because they omitted their
  underlying and used an expired session date. Their setup now supplies BTC and
  an upcoming session expiry, allowing their transaction checks to run.

## Rollout

Deploy the trading backend from main alongside the frontend. The new account-asset
endpoint and owner calendar require the updated API; no schema migration is needed.
Browser checks used synthetic local accounts and did not change real automation
preferences or submit exchange orders.

On October 12, 2026, the production trading API was rebuilt from main and only the
Delta-exchange container was recreated. The previous image is retained as
`delta-exchange-delta-exchange:before-owner-users-20261012` for rollback. Its health
check passed; the scheduler is running with no last error and both private account
streams reconnected. Public OpenAPI confirms the account-asset switch, owner
calendar and asset parameters on owner detail and trades. The public site returns
200 and serves the redesigned Users stylesheet. The final production frontend build
completed without warnings. The disposable database and test network were removed.

Signed-in production user interactions were not exercised. Interaction checks above
used the local fixture preview, which was removed before the final build.
