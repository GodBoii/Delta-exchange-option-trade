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

To be recorded after implementation.
