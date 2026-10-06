# Strategy preview authentication repair, 6 October 2026

## Diagnosis

Production records for 4 October through approximately 16:27 IST on 6 October contained
31 due reviews: 18 completed, 11 failed, and two cancelled for unavailable trade slots.
Ten failures were associated with the previously recorded OpenRouter key allowance
exhaustion. One BTC review on 5 October was interrupted by the backend restart.
The provider allowance had been restored when checked on 6 October.

After the successful ETH selection on 5 October, 16 reviews made 66 previews:
65 returned `Selected public option evidence is unavailable`, and one rejected an
invalid intraday holding window. The successful ETH strategy entered at 05:45 IST
on 5 October and exited at 12:45 IST, confirming that exchange execution worked.

The current preview blocker was service authentication. `Delta-exchange` and
`bitcoin-agent` had the same nonempty `ANALYSIS_SERVICE_SECRET`; both market
containers had an empty value. The market services rejected `/watch-expiries`
with HTTP 401 even though catalogue and selected-contract requests succeeded.
The preview tool converted that HTTP failure into a generic missing-evidence
message and did not cache a usable preview. Healthy market-stream checks did not
detect this authentication problem.

## Changes

- Compose requires a nonempty market-service secret. Deployment commands and
  the PowerShell launcher load `.env.local` and `backend/.env` for interpolation.
  Market services receive only their explicitly listed settings, without database
  or account credentials.
- Preview authentication errors carry a specific code and safe message. Logs
  identify the run, asset, endpoint path, and HTTP status without credentials.
- An authentication failure with no committed action fails the analysis instead
  of producing an ordinary no-trade outcome. The safe error survives the analysis
  worker boundary and appears in run history. Already committed proposals and
  follow-ups retain their outcomes.

Code commits: `595a9de` and `906f0b0`, pushed to `main`.

## Validation and deployment

Sixty focused backend tests and six market-evidence tests passed. Ruff,
`git diff --check`, and PowerShell syntax validation passed. Server Compose
validation confirmed matching nonempty secrets in all four services and rejected
an explicitly empty secret. No secrets were printed or committed.

The server pulled `main` in `/home/arun/apps/delta-exchange`. All four application
images were rebuilt, then their containers were recreated using:

```sh
docker compose --env-file .env.local --env-file backend/.env build delta-exchange news-analyzer binace binace-eth
docker compose --env-file .env.local --env-file backend/.env up -d --no-deps --force-recreate --wait --wait-timeout 120 binace binace-eth news-analyzer delta-exchange
```

No analyses or trades were active at the pre-deployment check. At approximately
16:40 IST, all four containers were healthy, with matching nonempty secrets and
zero restarts. The writer scheduler was running with no last error; its private
stream and database change feed were connected. Analyzer storage was ready.

The deployed `preview_strategy` tool successfully previewed a long ATM straddle
for each asset with a 16-hour overnight hold. Each returned two legs and cached
the preview for selection. These checks used no model requests, scheduled no
strategies, and submitted no orders. Both market services recorded successful
HTTP 200 expiry-watch requests, and logs contained no HTTP 401 or aggregate
watchlist synchronization failures since recreation.

The OpenRouter key still had a limited remaining allowance and no automatic
reset at the investigation check. This deployment did not change its spending cap.
