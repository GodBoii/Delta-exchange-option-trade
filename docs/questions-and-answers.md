# Research questions and verified answers

This file tracks side questions without interrupting the main documentation work. Answers are time-stamped because catalogue and repository behavior can change.

## Does Delta provide a list of straddle and strangle combo contracts?

**Verified 2026-08-08:** Yes, tradable exchange-listed products are discoverable through `GET /v2/products`. Delta India returns listed straddles under `contract_type=move_options`:

```http
GET https://api.india.delta.exchange/v2/products?contract_types=move_options&states=live&page_size=100
```

The live response contained six BTC `MV-...` products whose descriptions said “BTC Straddle.” The API/CCXT vocabulary also includes `spreads`, but the India query for live `spreads` returned zero. No separate live `strangle` type was observed.

So there is a dynamic list, not a permanent static table. Query `move_options` and `spreads`, inspect descriptions and product specs, and only treat something as an exchange combo if the API returns one product ID for it.

## Does this site apply stop loss per leg or on combined premium?

**Verified 2026-08-08:** It currently applies stop loss **per leg**.

`backend/app/engine.py` takes each leg's preview mark, adds the configured `stopLoss` for a short leg, and sends the result as that product's `bracket_stop_loss_price`. It never sums the call and put premiums. The `overallStopLoss` field exists but is explicitly reported as deferred and is not monitored.

Also, the `stopLoss` number is currently an absolute premium-price increment, not a percentage. Therefore entering `100` does not mean “100% of combined entry premium.”

For the requested short ATM straddle, the site needs a cross-leg monitor based on actual fills and current aggregate close cost, or a verified single listed combo product. See [This repository's Delta integration](11-project-integration.md) for the required design.

