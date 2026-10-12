# BTC, ETH and overall capital allocation

The account holder edits capital in Portfolio. The owner has the same controls in
Users for any registered user, including their own account. The owner endpoint
still requires the owner role; personal endpoints use the authenticated account.

## Rules and units

The default budget applies to both assets until an asset override is set. Every
budget funds one complete strategy, including all its legs. Percentage budgets
use the total USD wallet balance, never the remaining balance. Fixed amounts are
stored in USD; the interface converts amounts to the selected display currency.

Supported budget modes:

- Existing 100%, 50%, one third and 25% modes retain their behavior.
- Equal parts accepts a whole-number divisor from 1 through 100.
- Custom percentage accepts a finite value greater than zero and at most 100.
- Fixed amount accepts a finite positive USD amount.

BTC and ETH can independently override the default with any of these modes.
For a $10,000 wallet, BTC 20% budgets $2,000 per strategy and ETH 10% budgets
$1,000. These percentages are per strategy, not aggregate asset shares.

Optional overall and asset caps limit the sum of reserved strategy budgets.
A cap can be a percentage of total wallet capital or a fixed USD amount. Caps do
not earmark funds for future entries. Reservations under other logins sharing the
same Delta wallet count toward the entering account's limits. Each login retains
its own policy and concurrent strategy count.

Without an explicit concurrency setting, legacy modes retain their 1–4 limits,
equal parts uses its divisor, custom percentage uses floor(100 / percentage), and
fixed amount uses floor(total / amount). Counts stay between 1 and 100. Asset
overrides use a 100-strategy ceiling because budgets differ. An explicit limit
from 1 through 100 overrides the count calculation. Funds and caps still apply.
The interface's zero concurrency setting clears the explicit limit.

Budgets remain capped by the exchange's available balance. This preserves the
previous behavior, including smaller entries when a requested fixed amount is
unavailable. Aggregate caps reject an entry that does not fit; they do not silently
shrink its budget. Lot sizing retains the existing 2% buffer and margin/risk rules.
A capital budget is not a guarantee of maximum trading loss.

## Execution and scheduling

`backend/app/capital.py` owns the calculation. Entry, capacity admission, wallet
previews and owner reporting use the same asset rule. Scheduled commitments carry
their asset so BTC and ETH analysis cannot estimate each other's budgets using
the default rule.

The PostgreSQL wallet advisory lock still serializes reservations across aliases.
Reservations retain their asset and amount. Older rows derive the asset from their
strategy, falling back to BTC for legacy rows. A policy change between sizing and
reservation rejects that attempt before orders are submitted. An existing
reservation cannot be reused with a different budget.

Due BTC entries retain priority when an ETH reservation would prevent a BTC entry
that could otherwise fit. The check calculates the due account's BTC budget and
caps rather than assuming it equals the ETH budget. Future, expired, paused or
rejected BTC entries do not hold up ETH. Active trades are never closed or resized
by a policy edit. Exits, risk monitoring, fill accounting and automation switches
retain their existing paths.

## API and storage

Both `PUT /api/capital/settings` and
`PUT /api/owner/users/{user_id}/capital` accept the same settings:

```json
{
  "allocationMode": "equal_parts",
  "divisionCount": 20,
  "assetOverrides": {
    "BTC": { "allocationMode": "custom_percentage", "allocationPercent": 20 },
    "ETH": { "allocationMode": "custom_percentage", "allocationPercent": 10 }
  },
  "overallLimit": { "mode": "percentage", "value": 80 },
  "assetLimits": { "ETH": { "mode": "percentage", "value": 30 } },
  "maximumConcurrentStrategies": 10
}
```

Omitted extension fields preserve saved settings, allowing older clients to edit
the default without deleting asset rules. Empty override/asset-limit objects clear
those rules. Null overall/concurrency limits clear those settings. Unknown assets,
non-finite values, out-of-range percentages and fractional counts are rejected.

Responses include `capitalRevision`. Both editors submit it as `expectedRevision`.
A concurrent user or owner edit returns 409 and asks the editor to reload rather
than overwriting newer settings. Older clients that omit the revision retain their
existing update behavior.

`trade.users.capital` stores the policy in JSON. Entry records snapshot the complete
policy, effective asset rule and entry wallet balance. Migration
`backend/db/011_asset_capital_policy.sql` adds JSON columns to owner policy changes
and capital observations, preserving complete settings alongside existing scalar
reporting fields. Existing rows are left intact.

## Rollout and checks

Deploy the backend before the frontend. The trading writer applies migration 011
at startup. Existing policies need no backfill and retain their old sizing until
edited. Deploy between analyses using the repository's existing deployment flow.

Coverage includes legacy policies, unequal asset budgets, arbitrary divisors,
invalid requests, concurrent reservations, aggregate caps, changed policies,
BTC priority, owner authorization, personal edits and complete audit snapshots.
Database tests require an isolated `TEST_LOCAL_DATABASE_URL`; never point these
tests at production.

Verification on October 12, 2026:

- The full backend run passed 716 tests against fresh PostgreSQL. The eight ETH
  catalog tests also passed against their separate disposable database.
- `test_local_library_and_private_research_endpoint` still fails because its
  incomplete strategy fixture receives 422. The same failure was reproduced from
  the unchanged baseline checkout.
- All 46 frontend tests, TypeScript checks, ESLint, changed-file Ruff checks and
  the production build passed.
- The actual Portfolio and owner Users components were exercised with local
  fixture data at desktop and mobile widths. Checks covered separate percentages,
  owner saves, inheritance, keyboard selection, invalid input and error feedback.
  Authenticated API and reservation behavior were verified separately against
  PostgreSQL; no production balances or orders were used for verification.
