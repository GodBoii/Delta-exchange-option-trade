# Strategy filtering and daily P&L

The personal P&L report has a strategy selector beside Accounting state. Strategy selection applies to performance totals, the daily calendar, both existing charts, and every trade-list page. Accounting state continues to filter only the list. One trade in the headline count is one strategy run, including runs that have not settled; the settled count and exclusion note explain which runs contribute to financial totals.

The selector lists distinct recorded strategy names from the signed-in account's entire visible ledger. Repeated runs with the same name are grouped together. Renamed strategies appear under their historical names, and separate saved strategies sharing a recorded name are combined. Options do not disappear when a period or asset contains no matching runs.

## Calendar accounting

`GET /api/me/pnl/calendar` aggregates every matching settled ledger row in PostgreSQL. It has no trade-page cap. Dates use `activity_at` in `Asia/Kolkata`; closed runs are filed under their exit execution time, with the existing entry/creation fallback for historical records. Money remains USD decimal strings on the wire, and the existing currency provider formats display amounts. Realized P&L already deducts exchange fees.

Green and red show daily net profit and loss. Both signs share four magnitude levels relative to the largest absolute daily result in the visible calendar. No activity and settled break-even activity have separate neutral styles. Hover/focus details and a persistent readout expose amounts and counts without relying on color. Arrow keys move through the calendar, Home/End reach the period boundaries, and future dates are disabled.

The calendar follows asset and strategy selection and reads complete history independently of the report period. Desktop screens show a complete January-to-December year with a year selector. Screens up to 767 pixels show a conventional monthly calendar with month and year selectors. The default is the current IST year and month; future months in the current year are disabled. The calendar subtotal covers only its selected year or month. Headline totals, the existing charts, and trade rows retain their separate report period.

## Compatibility

- `strategy` is optional on `/api/me/pnl` and `/api/me/trades`. Responses echo the applied strategy, and the frontend rejects ignored filters.
- `/api/me/pnl/strategies` lists account-scoped historical names independently of period and pagination.
- Personal reports continue excluding user-deleted runs and manual Delta trades. Only settled runs contribute money and win/loss counts.
- Owner reports retain their existing filters and deletion policy. Shared frontend additions default to the previous behavior.
- The existing charts still have their explicit 1,000-run cap and warning. Calendar and headline totals cover every matching run.
- No database migration, trading execution change, or new runtime dependency is required.

## Verification

Verified on October 10, 2026:

- 44 backend accounting, reporting API, and ledger tests passed against isolated PostgreSQL on `ubuntu-server`. Tests cover strategy pagination, account isolation, deleted runs, midnight IST, and totals reconciliation.
- 39 frontend tests passed. Type checking, source lint excluding Python virtual-environment vendor files, and the production Next.js build passed.
- Read-only reconciliation on the deployed database checked money and outcome counts across 93 account/strategy/asset scopes.
- Local browser fixtures verified desktop and 390-pixel layouts, strategy and accounting filters, full-year calendars, keyboard navigation, empty strategies, trade-detail dialogs, and calendar failure isolation. A fresh page produced no browser warnings or errors. The temporary fixture was removed from the final source.
