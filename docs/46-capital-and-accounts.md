# Capital and accounts

Capital and account routing now share a workspace. Portfolio shows a compact
capital summary and opens this workspace; Automation has the same shortcut.
The existing `subaccounts` tab identifier and API paths remain compatible.

## User behavior

The default editor offers percentage or fixed budgets, a total allocation cap,
a positive simultaneous-strategy count, and an explicit funding-shortage choice.
BTC and ETH budgets and caps remain available under advanced settings. Exact
legacy fraction and equal-part calculations remain unchanged until edited.
Editing a budget materializes the previously displayed strategy count so adding
an asset override does not silently change it to 100. Untouched policies retain
their existing backend count calculation.

The receipt estimates the selected asset's next budget, account, and transfers.
Unsaved settings are identified as proposals. Requests are debounced and obsolete
responses are discarded. Account refreshes retain an unsaved budget and its
original capital revision so another editor's changes still cause a save conflict.
Mobile has a sticky amount summary and a link to the full receipt. Motion respects
reduced-motion preferences. Shared controls support the existing light theme.

Trade location and funding are a compact disclosure. Main-account percentages
use the main wallet. Sub-account modes use the managed pool. Participation changes
can change that pool; active assigned accounts remain included. Existing assigned
runs retain their account when routing settings change. Manual assignment remains
an advanced flow and explicitly explains that automatic allocation pauses.

Automatic funding may top up a selected child, return its pre-entry surplus, and
reuse eligible idle funds through Main. Finishing a run does not sweep its funds
back to Main. Existing transfer and release protections remain authoritative.
Transfers, permanent assignments, position closes, order cancellations, and key
removal use account-specific confirmation dialogs. Unconfirmed transfer checks
are not reported as successful reconciliation.

## Backend behavior

`POST /api/capital/preview` accepts the capital settings contract plus an optional
BTC or ETH `asset`. It validates and merges the proposed settings with the saved
policy, then reads wallet, allocation and account state. It does not save settings,
record wallet observations, reserve accounts, or dispatch transfers. Its response
includes an estimated account and transfer list, or an explanatory blocking reason.
This is an estimate for a new unassigned run, not a reservation or order guarantee.
Entry still verifies scheduling, contracts, live funds and exposure under the
existing reservation and transfer locks.

The preview, automatic account selection and automation capacity admission share
the live account candidate checks. Already-funded candidates are preferred.
Without automatic funding, an insufficiently funded child is skipped before an
immutable assignment is created. Automation checks estimated fundability before
admitting analysis.

`allowSmallerEntries: false` requests the full strategy budget or no entry.
The field defaults to the existing smaller-entry behavior for old policies.
Old clients omitting the field preserve a saved setting. The JSON-backed policy
store needs no new database migration. Full policy snapshots retain the setting.

## Rollout and verification

Deploy the backend before the frontend, which uses the new preview endpoint and
shortage field. Existing saved policies, reservations, active runs, account
identities, funding journals and recovery rules remain in place.

Verification used frontend unit tests, TypeScript, ESLint, a production build,
backend tests and changed-file Ruff checks. Browser checks used the real components
with an injected fixture requester at mobile, tablet and desktop sizes. They
covered edits, saves, invalid input, asset overrides, preserved strategy counts,
account expansion, transfer confirmation and Escape cancellation, light theme,
reduced motion, and overflow. The temporary fixture route was removed before the
final build. No real trading settings, credentials, transfers or orders were used.
PostgreSQL-dependent tests require a disposable `TEST_LOCAL_DATABASE_URL` and were
skipped in this environment.
