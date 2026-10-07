# Ubuntu monitor operations

The standalone monitor was started on 8 October 2026 using the dedicated X account `@ClothWala28`. Email and passwords are not part of the configuration. Session cookies were imported privately and are stored only in local ignored state and the dedicated Docker volume. Temporary import files were removed.

## Deployment

- Host connection: `ssh ubuntu-server`
- Source: `/home/arun/apps/x-social-monitor/social_monitor`
- Compose project: `delta-social-monitor`
- Container: `delta-social-monitor-monitor-1`
- State volume: `delta-social-monitor_monitor-state`
- Watched profiles: `elonmusk` and `realDonaldTrump`, including replies.
- Poll interval: 120 seconds per target after its previous poll completes.
- Limits: 256 MB memory and 0.5 CPU, unprivileged user, read-only root filesystem, no published ports.

This deployment is independent of the trading checkout, trading database and existing application containers. The source was transferred as an archive of tracked monitor files. Future changes need to be transferred and rebuilt explicitly; pulling the trading checkout does not update this deployment.

## Inspect and stop

After connecting to Ubuntu:

```sh
cd /home/arun/apps/x-social-monitor/social_monitor
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs --tail 30 monitor
docker compose -f compose.yaml exec -T monitor python -m social_monitor --config /monitor/config.toml status
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

For expired sessions, stop this monitor, renew the dedicated account's session through private local input, and transfer it over SSH using a private file or stdin. Never put cookie values into shell arguments, source control, screenshots, support tickets or chat. Do not copy a personal account session into this deployment. Do not remove the state volume during ordinary restarts, since it contains both credentials and deduplication history.
