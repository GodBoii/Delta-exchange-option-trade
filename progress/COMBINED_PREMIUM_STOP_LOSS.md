# Combined Premium Stop Loss

## Goal

Turn the short call and short put into one risk unit: measure their filled entry credit together, trigger at a percentage of that combined credit, and close both live legs with reduce-only orders.

## Progress

### 2026-08-08 — Started

- Audited the existing leg-wise bracket calculation, scheduler, exit flow, Delta client, database schema, strategy models, and builder UI.
- Verified that Delta bracket controls belong to one product and that the batch endpoint cannot combine a call and put or submit stop orders.
- Selected an application-side combined-premium monitor with exchange-side emergency per-leg protection.
- Added explicit strategy fields for risk mode, combined stop percentage, and emergency stop percentage.
- Changed validation so leg-wise shorts still require individual stops while combined-premium strategies require at least two short legs and a combined percentage.
- Added a database migration for persisted risk snapshots, trigger timestamps, and reconciled fill values.
- Chose a low-copy visual risk workflow: two mode tabs, a call-and-put convergence diagram, a 1× entry / 2× exit threshold, and a paired-exit state.
- Selected sliding-tab motion for the risk-mode switch and grid-row accordion motion for leg details, including reduced-motion behavior.

### 2026-08-08 — Backend implementation

- Added authenticated Delta helpers for fills, one real-time product position, product metadata, and one ticker.
- Reconciled each entry order against exchange fills and persisted filled quantity, weighted average fill price, and commission.
- Added Decimal-based combined-premium math using side, filled size, entry price, mark price, and contract value.
- Armed combined protection only after every entry order is fully filled.
- Added fresh-mark validation so a missing or zero mark cannot trigger a decision.
- Added a durable scheduler pass for every active combined-premium strategy.
- Persisted entry credit, current close cost, loss, trigger close cost, percentage progress, leg fill state, and monitor timestamps.
- Added a database claim before exit submission so the combined stop triggers once.
- Changed exits to read real-time positions per product, cap quantity to the strategy-owned amount, verify direction, and submit both reduce-only market exits concurrently.
- Added an attention state when an already-triggered exit cannot be submitted cleanly.
- Added optional wide emergency per-leg brackets, expressed as a percentage in the strategy UI.

### 2026-08-08 — UI and motion implementation

- Replaced the old default spread with a short ATM call + short ATM put.
- Replaced the inactive overall-stop field with a visible Combined / Per leg mode selector.
- Added a low-copy risk diagram showing both sell legs converging on one percentage shield and one paired buy-to-close action.
- Added live 1× entry and calculated exit-multiple tiles; a 100% stop displays a 2× exit threshold.
- Added the emergency backup percentage as a distinct amber control so it cannot be confused with the combined stop.
- Hid individual target/stop/re-entry controls while combined mode is active; each leg now shows that it is protected by the combined trigger.
- Added a Combined protection panel to the dashboard with awaiting-fills, armed, triggered, exit-submitted, and attention states.
- Added a sliding active pill to segmented controls and replaced measured-height leg animation with a grid-row accordion.
- Verified the accordion fully collapses to 0 px and keeps only one leg open.
- Added responsive layouts for desktop, tablet, and mobile plus reduced-motion fallbacks.
- Rendered the real builder in a temporary development-only preview, inspected desktop and 390 px mobile layouts, exercised the mode switch, and removed the preview bypass afterward.

## Safety decisions

- Combined risk is calculated from actual fills, never preview marks.
- The trigger is latched in the database before exits are sent.
- Exit quantities are bounded to strategy-owned entry quantities and use `reduce_only`.
- Real-time product positions are used for exit reconciliation instead of the lagging margined-position collection.
- A one-leg or partial-fill state remains unarmed and visible for reconciliation.

## Validation log

- `npm run typecheck` — passed.
- `npm run lint` — passed with no warnings.
- `python -m ruff check backend/app backend/tests` — passed.
- `python -m pytest backend/tests -q` — 9 passed.
- `npm run build` — production build passed.
- Desktop risk grid measured inside the viewport with no horizontal overflow.
- Mobile risk control measured at 303.6 px inside a 390 px viewport; document scroll width remained within the viewport.
- Combined / Per leg transition and accordion interaction verified in the rendered application.

## Deployment required

- Apply `supabase/migrations/002_combined_premium_risk.sql` before starting the updated backend. The engine and dashboard expect its new columns.
- Rebuild and restart the single backend container after the migration.
- Use a testnet or non-production account for the first exchange-connected end-to-end fill and trigger drill. Automated tests do not place real orders.
