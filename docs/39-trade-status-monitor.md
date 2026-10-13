# Trade status monitor

## Existing behavior and implementation plan

The owner Users view reads account-scoped owner ledger records and retrieves a
sanitized run detail from the engine when the run still exists. Deleted runs use
the retained archive. Strategy history already has an accessible row menu.
Personal and owner P&L share TradeTable. The engine owns execution and risk checks;
its account sessions already receive private events and public contract marks.

1. Add sanitized monitor observations behind existing owner and personal auth.
2. Add Details and View to trade menus and retain existing strategy actions.
3. Build a read-only window with status, risk valuation, contract marks, recorded
   fills and execution activity. Refresh without submitting or reconciling orders.
4. Retain existing theme tokens, portalled menus, modal focus handling, transitions
   and reduced-motion support. Verify desktop and mobile layouts.
5. Test isolation, archive behavior, unavailable prices and stale valuations; run
   frontend/backend checks, push one file per commit, then deploy the trading API.

## Data and timing

- `GET /api/owner/users/{user_id}/trades/{run_id}/monitor` rechecks the owner role
  and scopes both the ledger and live run lookup to the selected user.
- `GET /api/strategies/{run_id}/monitor` scopes the run lookup to the caller.
- Both return a sanitized run, observation timestamp, private-stream availability
  and contract marks for symbols recorded on that run. No credentials, raw exchange
  bodies, internal account identifiers or wallet data are included.
- Open windows request a snapshot every two seconds after the previous request
  completes. Hidden tabs pause requests; closing aborts them. Terminal and archived
  results stop automatic requests. Refresh remains available.
- Contract marks must be at most 15 seconds old. Engine risk valuations usually
  persist about every 10 seconds; their own timestamp is displayed. Missing or
  older-than-30-second valuations are marked unavailable/stale. Gross risk P&L
  excludes fees and must not be interpreted as realized profit or an exit quote.
- Interrupted observations suppress live marks; completed and archived windows
  label live prices stopped instead of retaining a price under a live label.
- Viewing uses existing sessions only. It does not create a private exchange
  session, change the scheduler, submit orders, or trigger reconciliation.

## Interface

Trade rows in Users and personal P&L have a three-dot menu. Details opens the
existing historical record. View opens the status window and offers a shortcut
back to Details. Strategy history adds View alongside its existing actions.
The existing menu and dialog primitives supply focus trapping, keyboard movement,
Escape/outside dismissal, focus restoration, collision handling and motion.
Row menus return focus to their trigger before opening an action dialog so closing
the dialog can restore focus to a control that remains mounted.

Owner monitoring is observational. Account automation and all existing exit,
cancel and delete rules remain owned by their existing workflows.

## Verification

- The backend suite passed 652 tests with 102 database-dependent tests skipped in
  that run. An additional focused run against disposable PostgreSQL 17 passed 52
  tests covering owner/personal authorization, cross-account 404s, archives,
  sanitization, accounting and existing portfolio streams.
- All 49 frontend tests, TypeScript, application ESLint and focused Ruff checks
  passed. The production Next.js build passed after removing the preview fixture.
  Repository-wide `npm run lint` also scans untracked `.venv` dependencies and
  reports pre-existing matplotlib JavaScript errors there.
- Local synthetic browser checks covered 1440px desktop and 390px mobile, dark
  and light themes, keyboard menu navigation, Escape, focus return and trapping,
  existing cancellation confirmation, Details, archives, stale risk values,
  backend failures, retained observations and automatic recovery. No horizontal
  page overflow or browser warnings/errors occurred in the checked states.
- Monitoring did not submit exchange orders or change automation preferences.
  Signed-in production account interactions were not exercised.
