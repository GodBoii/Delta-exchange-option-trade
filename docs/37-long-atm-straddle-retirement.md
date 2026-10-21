# Long ATM straddle retirement

Implementation plan, October 21, 2026.

1. Remove the built-in BTC template and frontend legacy default name. ETH long
   straddle templates are already retired.
2. Identify legacy names and renamed two-leg ATM call-and-put buy pairs in one
   shared policy. Preserve short straddles, OTM strangles, single options, and
   calendar pairs with different expiries.
3. Exclude retired strategies from AI discovery and reject stale references,
   backend proposals, shared allocations, and new trade entries. Keep historical
   reads and position exits intact.
4. Add an idempotent `retire-long-straddle` operation to `scripts.shared_library`.
   Dry-run first. Soft-delete matching templates for every owner, set both AI
   flags false, and increment versions. Retain run and order records.
5. Verify remaining templates, selection rejection, entry rejection before any
   exchange access, database dry-run, idempotency, reseeding, and history retention.
   Replace positional catalog references in affected tests with names.
6. Commit each file separately on `main` and push to `origin main`.
7. On `ssh ubuntu-server`, pull `main` in `/home/arun/apps/delta-exchange` and build
   `delta-exchange` and `news-analyzer`. Stop both writers, run the retirement
   command in the new backend image, and restart both services. Check database
   flags, remaining catalog, scheduler health, and logs.

The deployed database is PostgreSQL on Ubuntu. No Supabase migration is needed.
The retirement command changes templates only. Existing proposal version and
deletion checks reject pending selections; the entry guard also blocks scheduled
copies that no longer reference a template.

Run from the server repository, using its existing environment files:

```sh
docker compose --env-file backend/.env run --rm --no-deps delta-exchange \
  python -m scripts.shared_library retire-long-straddle
docker compose --env-file backend/.env run --rm --no-deps delta-exchange \
  python -m scripts.shared_library retire-long-straddle --apply
```

## Completed verification and deployment

- Backend suite passed locally and in a Linux Python 3.13 test image, with 816
  passed and 137 database-dependent tests skipped in each run.
- The isolated PostgreSQL and retirement regression run passed all 21 tests.
- Changed Python files passed Ruff. Frontend typecheck and all 70 frontend tests
  passed.
- Pushed each changed file in its own commit to `origin main`. Pulled `main` on
  Ubuntu, rebuilt the backend and analyzer, and restarted both after retirement.
- Updated three template records. All four stored BTC and ETH long ATM straddle
  variants now have `deleted=true`, `enabled_for_ai=false`, and JSON
  `enabledForAi=false`. A second retirement pass made no changes.
- Compared all 148 strategy run records before and after the retirement
  transaction. They were unchanged. Template backup is in the persistent backend
  volume at `/app/state/long-atm-straddle-retirement-20261021.json`.
- Read the live writer library through the deployed AI discovery tool. BTC lists
  nine strategies and ETH lists eight, with no retired long straddle. A stale
  reference to the BTC long straddle was rejected before scheduling.
- Both Docker services reported healthy. Backend trading and automation
  schedulers reported running with no last error. The analyzer reported its
  database ready. Removed the disposable test database and staged test artifacts.
