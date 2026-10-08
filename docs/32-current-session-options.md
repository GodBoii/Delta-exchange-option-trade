# Current-session options and short holding limits

October 8, 2026. New decisions focus on the upcoming 17:30 IST expiry. At or after
17:30, the session rolls to the following calendar day. The choice is the next
session boundary, not an arbitrary next listed contract. If that expiry lacks
the required legs or strike steps, the preview fails and the agent must correct
its candidate or record no trade.

| Review | Maximum preset | Analysis instruction |
|---|---|---|
| Asia | 7 hours | Primarily assess strategies suited to today's expiry |
| London | 7 hours | Assess the remaining hours before expiry |
| Pre-expiry | 7 hours, shortened to expiry buffer | Only the short remaining same-day window |
| New York | 7 or 11 hours | Upcoming expiry; midnight crossing allowed |
| Midnight, manual and follow-up | 7 hours | Upcoming expiry and the remaining permitted window |

Seven and eleven hours are maxima from planned activation, not guarantees of a
full holding window. Presets shorten to the saved expiry buffer. A 15:30 IST
review starts two hours before expiry; processing, activation lead time, recheck
and the safety buffer reduce the actual hold. Explicit custom exit timestamps
must fit both the duration limit and the current session. No overnight,
positional, first-expiry or second-expiry holding choice remains. Review times
for London and New York continue following their local daylight-saving rules.

## Enforcement

`app.exit_schedule` owns preview resolution and schedule validation. Agent tools
carry their review trigger and snapshot time into it. The writer validates new
proposals again against the persisted job's trigger and scheduled time, so an
agent argument cannot grant itself the evening exception or defer to a later
session. Manual scheduling uses the same resolver with the seven-hour limit.
The UI exposes up to seven hours or a bounded custom exit and explains that
late-session holds shorten.

The implementation does not rewrite saved risk controls, quantities, account
allocation, stops, targets, active positions, pending legacy schedules, or
historical reports. Existing assigned-expiry rechecks remain possible. Historical
definition readers and legacy replay helpers retain their old enums because
stored trades still need to be read and reconstructed.

## Analysis evidence

The default market-service option overview contains only the upcoming expiry,
with no one/three/seven/thirty-day selection. Explicit assigned expiries remain
available for rechecks of pre-existing proposals. The backend also filters the
main agent's option overview defensively by its snapshot's session boundary.
The stored/private catalogue and historical collection keep their wider data
for contract resolution, existing exposure, audit and replay; this is not
additional data passed into new strategy analysis.

The strategy catalogue strips stale entry, holding and expiry fields from saved
definitions before showing them to the agent. Numerical starting comparisons
resolve at the upcoming expiry. Starting movement scales use that same expiry;
after preview, `selectedLegMoveScales` identifies each selected symbol, IV,
expiry and actual holding duration. The instructions require the agent to use
those selected-leg values rather than mix them with another maturity. These
are movement scales, not profit forecasts or early-exit breakevens.

Existing Spot indicators, session history, futures, news research, calculator
tools, no-trade behavior and follow-up scheduling continue. No active-position
thesis monitor or new account-equity loss rule is introduced by this change.

## Validation and rollout

Focused tests cover BTC/ETH, expiry rollover, the evening exception, late-session
shortening, missing current-expiry contracts, custom-exit bypass attempts, and
writer rejection without committing a proposal. The market-data suite covers
single-expiry summaries and explicitly assigned legacy contracts. Frontend
tests, type checking, lint and production build validate the scheduling UI.
Browser verification timed out and is not claimed.

The focused backend suite passed 99 tests with the isolated PostgreSQL fixture
enabled. The full market-data suite passed 39 tests; the frontend suite passed
32 tests. Type checking, focused lint and the production build passed. A broader
backend sweep initially passed 557 tests and failed six: two missing-timestamp
mocks were corrected and rerun, while four unrelated library/research fixture
failures reproduced against unchanged pre-change commit `fdccd86`.

All services require the same analysis secret during Compose interpolation.
After pulling `main` on Ubuntu, rebuild/start the complete stack with both env
files supplied:

```sh
docker compose --env-file .env.local --env-file backend/.env up --build -d
```

This performs the requested Compose build/start without attaching to ongoing
container logs. No post-deployment log monitoring is part of this rollout.
