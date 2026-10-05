# P&L market filter deployment fix

Verified October 5, 2026.

The frontend sent `asset=BTC` or `asset=ETH`, but the running trading API image predated the filter implementation. Its personal reporting routes accepted no asset parameter. FastAPI ignored the unknown query parameter and returned combined totals and trades, so every toggle displayed the same calculations.

The backend image was rebuilt from `main` and the `delta-exchange` service was recreated. The running `/api/me/pnl` and `/api/me/trades` routes now accept `asset`. Both filter the ledger before aggregation or pagination. Missing historical asset values remain BTC, matching the existing trade display. Only settled runs contribute to realized profit, gains, losses, fees and win rate.

Personal reporting responses now echo `asset` as `all`, `BTC` or `ETH`. The frontend checks this field for summary requests, every chart page and every trade-table page. An API that ignores a requested market produces an error instead of displaying combined amounts under that market. Existing unfiltered owner reports remain compatible with responses that omit this field.

## Verification

- All 28 reporting API and ledger tests passed against a disposable PostgreSQL 17 database, including mixed markets, legacy BTC rows, fees, deleted runs, account isolation, pagination and empty results. The disposable container and network were removed.
- All 32 frontend tests passed. Type checking, focused ESLint, Ruff and the production Next.js build passed.
- A read-only check against recorded production trades verified each market's totals, gains, losses, fees, wins and losses against its own paginated rows. Combined totals equal BTC plus ETH for the all-time and 30-day periods.
- The signed-in production dashboard was checked with All, BTC and ETH. Summary tiles, the cumulative line, the gains/losses bars and the trade table changed together. Refresh retained the selected market. Desktop and 390-pixel layouts rendered the charts without horizontal overflow, and the browser reported no errors or warnings.
- The recreated API container passed its health check and advertised the asset parameter on both reporting routes.
