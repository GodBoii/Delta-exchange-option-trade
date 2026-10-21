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
