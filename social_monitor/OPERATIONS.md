# Ubuntu monitor operations

The standalone monitor was started on 8 October 2026 using the dedicated X account `@ClothWala28`. Email and passwords are not part of the configuration. Session cookies were imported privately and are stored only in local ignored state and the dedicated Docker volume. Temporary import files were removed.

## Deployment

- Host connection: `ssh ubuntu-server`
- Active source: `/home/arun/apps/x-social-monitor/current`, a symlink to the deployed release.
- Version 0.1 source retained for rollback: `/home/arun/apps/x-social-monitor/social_monitor`.
- Compose project: `delta-social-monitor`
- Container: `delta-social-monitor-monitor-1`
- State volume: `delta-social-monitor_monitor-state`
- Watched profiles: `elonmusk` and `realDonaldTrump`, including replies.
- Version 0.2 poll interval: 15 seconds per target, scheduled from poll start.
- Routine reads: stop after a page contains three known post IDs, with deeper bounded checks every 900 seconds.
- Limits: 256 MB memory and 0.5 CPU, unprivileged user, read-only root filesystem, no published ports.

This deployment is independent of the trading checkout, trading database and existing application containers. The source was transferred as an archive of tracked monitor files. Future changes need to be transferred and rebuilt explicitly; pulling the trading checkout does not update this deployment.

## Inspect and stop

After connecting to Ubuntu:

```sh
cd /home/arun/apps/x-social-monitor/current
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs --tail 30 monitor
docker compose -f compose.yaml exec -T monitor python -m social_monitor --config /monitor/config.toml status
docker compose -f compose.yaml exec -T monitor python -m social_monitor --config /monitor/config.toml stats --hours 24
docker compose -f compose.yaml stop monitor
```

To start it again:

```sh
docker compose -f compose.yaml up -d --no-build
```

The restart policy is deliberately `no`. It will not automatically restart after a container exit or server reboot. Check status and logs before restarting a failed collector.

## Initial verification

The first collection stored 40 Elon Musk posts and 21 Trump posts. The Trump batch reported an upstream warning. A diagnostic retry fetched 40 Trump posts without warnings, and the first continuous cycle completed successfully for both targets. Stored post count after that cycle was 80, with no pending alerts. Initial batches establish baselines rather than broadcasting historical posts.

Both initial timelines filled the 40-post batch. This is recorded in diagnostics and does not prove complete timeline coverage. New posts are collected on later cycles; stale posts do not produce alerts. Cookie-based collection remains unofficial, can break, and can result in account restrictions. Using a dedicated account does not change X's rules or guarantee account safety.

## Version 0.2 rollout

The 9 October update uses versioned source releases and retains the previous Docker image as `delta-social-monitor-monitor:before-e652b5a`. Evidence schema changes are additive. The existing state volume and dedicated account session are reused. An SQLite backup is taken before replacement, and migration checks run on a copy of live evidence first.

The active code release is `0991397`. All 32 tests passed on Windows and in the Ubuntu Docker image. Migration of a live evidence copy preserved 266 posts, 184 alerts and both target baselines. The first eight incremental checks all succeeded, using one returned page each, with a median duration of 0.92 seconds and maximum of 1.37 seconds. Observed memory was approximately 53 MB. Both targets also passed collection after the final authentication fix was deployed.

A deep Trump read reproduced `pagination_stalled`; the following first-page checks succeeded. This explains a confirmed deep-pagination failure, not every historical empty result. Historical pagination remains bounded and periodically checked. Container IDs and start times for all 19 other running services were unchanged by deployment.

Status includes the newest returned post's age, independently from the poll's age. A healthy request returning an old post is not proof of missing posts or of a new event. The original 67-second median and 122-second p95 alert delays describe version 0.1; use new alert samples to evaluate version 0.2 rather than assuming an eightfold cadence improvement guarantees an eightfold delivery improvement.

No newly published post had been observed during rollout verification, so the new publication-to-alert latency has not yet been measured. Request duration and polling interval are separate from publication-to-alert delay.

For expired sessions, stop this monitor, renew the dedicated account's session through private local input, and transfer it over SSH using a private file or stdin. Never put cookie values into shell arguments, source control, screenshots, support tickets or chat. Do not copy a personal account session into this deployment. Do not remove the state volume during ordinary restarts, since it contains both credentials and deduplication history.
