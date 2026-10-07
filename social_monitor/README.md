# Independent X monitor

This is a separate read-only process for watching X accounts, lists and Latest searches without X developer API keys. It uses Twscrape 0.20.1 with a session you authorize locally. It never imports the trading backend, reads its environment files, connects to its databases, or submits orders. The root Compose configuration is unchanged.

## What works

- Configurable account, reply, list and search collection.
- SQLite evidence storage with publication time, batch receipt time, author IDs, post links, reply/quote/repost IDs, quoted/reposted text and media URLs.
- Candidate cashtag, EVM address and 32-byte Solana address extraction. Addresses are candidates, not verified token contracts.
- JSON alerts on stdout, diagnostics on stderr. Posts without token mentions also produce alerts so new memes are retained.
- Persistent deduplication across targets and restarts, a durable alert outbox and one writer per state directory.
- First-poll baselines, stale-post filtering, per-target timeout and exponential retry backoff.
- Local status, JSONL export and a replay command that needs no account or network.

This version collects evidence. It does not yet perform semantic meme interpretation, token verification, price tracking, Reddit collection or trading. Those can consume the evidence later without changing the collector or the existing trading system.

## Windows setup

Run these commands in PowerShell from the repository directory. The environment is separate from the backend environment.

```powershell
cd social_monitor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item config.example.toml config.local.toml
.\.venv\Scripts\python.exe -m social_monitor --config config.local.toml validate
```

Edit `config.local.toml` to select accounts. State paths are relative to the configuration file. The example watches two accounts at a two-minute interval; this is a starting setting, not a promise of subsecond collection. More accounts increase requests and the duration of a collection cycle. Targets run sequentially to bound load.

### Check the pipeline without X

```powershell
.\.venv\Scripts\python.exe -m social_monitor --config config.local.toml --state-dir state/demo replay examples/posts.jsonl --at 2026-10-08T12:01:00+00:00
.\.venv\Scripts\python.exe -m social_monitor --config config.local.toml --state-dir state/demo status
.\.venv\Scripts\python.exe -m social_monitor --config config.local.toml --state-dir state/demo export
```

The fixture is synthetic. The first replay emits three alerts; a second replay into the same demo database emits none. Replay uses the supplied receipt time. A demo database is separate from live state; do not replay into a live evidence database. Status compares stored receipt times to the system clock, so old fixtures will appear stale and do not establish health for configured live accounts.

### Authorize live collection

Sign in to your own X account in your browser. Open the browser's developer tools, then Application or Storage, then Cookies for `https://x.com`. Find `auth_token` and `ct0`.

```powershell
.\.venv\Scripts\python.exe -m social_monitor --config config.local.toml auth
```

Enter the two cookie values at the hidden prompts. Do not paste cookies into chat, command arguments, Git, screenshots or a third-party bot. Only these two cookies are imported. The command requires an interactive terminal; it will not fall back to visible input. A saved session is not proof of working access.

Session data is in `state/accounts.sqlite`. Public evidence is in `state/monitor.sqlite`. State, local configuration and the environment are ignored by Git. POSIX permissions are restricted where supported; on Windows, protect the state directory with your user account's filesystem permissions. Evidence exports and alert logs contain public post text, not the cookie database.

```powershell
.\.venv\Scripts\python.exe -m social_monitor --config config.local.toml run --once
.\.venv\Scripts\python.exe -m social_monitor --config config.local.toml run
```

Keep the foreground process running for continuous monitoring. Stop with Ctrl+C. There is no scheduled task or automatic startup. Alerts are JSON Lines on stdout; diagnostics use stderr. You can redirect them to separate local files. A flushed stdout alert means the process handed it to its output stream, not that a person or external service received it.

The first successful poll of each new target saves its current posts without historical alerts. Later fresh posts produce alerts. Set `alert_on_first_poll = true` to alert for recent posts on startup too. Deduplication persists, so changing that option does not re-alert stored posts. Renew expired cookies with `auth` after stopping the writer.

## Linux or standalone Docker

For Linux, the same CLI works after `python3 -m venv .venv` and `.venv/bin/python -m pip install -e .`.

The optional Compose project has its own volume and network, no published ports, and no connection to the trading services. It limits CPU and memory and does not restart automatically. Run it from this directory, not through the repository's main Compose file. Set `state_dir = "state"` in the mounted configuration.

```sh
docker compose -f compose.yaml build
docker compose -f compose.yaml run --rm monitor validate
docker compose -f compose.yaml run --rm -it monitor auth
docker compose -f compose.yaml run --rm monitor run --once
docker compose -f compose.yaml up -d
docker compose -f compose.yaml logs -f monitor
docker compose -f compose.yaml exec monitor status
docker compose -f compose.yaml down
```

`down` preserves the evidence volume. The container uses the example configuration; edit that file locally or change its bind mount to your local configuration before starting.

## Health and coverage limits

- `run --once` returns 0 when all target collections complete, 2 for a failed/partial collection, and 130 on interruption. Missing authorization fails before collection.
- `status` shows the latest attempt per target, failure code, stale status, unpolled targets and pending alerts. It reads evidence without accessing X or exposing account data. `healthy` requires a recent successful attempt for every configured target. It does not prove complete X coverage.
- Twscrape can end an iterator on both an empty timeline and an upstream failure. This monitor records no-post results as `empty_unverified`, not verified healthy coverage. A genuinely quiet or inaccessible account may therefore remain unhealthy.
- A full batch produces `batch_limit_reached` in diagnostics. Increase the batch or narrow a noisy target if it regularly fills; bounded polling can miss posts during high volume or outages. Search indexing and timeline ordering also affect coverage.
- Timeouts and failures are retried at increasing intervals up to one hour. No account purchase, CAPTCHA bypass or rate-limit evasion is implemented. X can restrict automated sessions or change its internal endpoints.
- Receipt time is measured at batch completion, not an exact per-post network arrival time. The collector does not promise a trading lead or an exhaustive historical backfill.
- Outbox delivery is at least once. A crash between stdout flush and acknowledgement can repeat an alert; consumers should deduplicate by `alert_id` within this database or by post ID across databases.
- Evidence is retained until you manage the state directory. Monitor disk usage for a long-running installation.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

Tests exercise real SQLite persistence, deduplication, baseline behavior, failed delivery recovery, timeout/cancellation, credential-safe failures, process locking, configuration validation, replay/export and the installed collector's missing-session behavior. Authenticated collection must be verified after you supply your own session.

The Windows test suite and replay were verified during implementation. Compose configuration validation passed. The Docker image was not built or run because the local Docker engine was unavailable. No deployment or restart commands were run for the existing services.

Upstream collection library: [Twscrape](https://github.com/vladkens/twscrape).
