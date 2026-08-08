# Products and instrument discovery

Product discovery is the source of truth for what can be traded now. Never generate a symbol and assume it is listed.

## Catalogue API

```http
GET /v2/products
```

Useful query parameters:

| Parameter | Meaning |
|---|---|
| `contract_types` | Comma-separated types such as `perpetual_futures,call_options,put_options` |
| `states` | `upcoming`, `live`, `expired`, `settled` as supported |
| `expiry` | Date filter documented as `YYYY-MM-DD` |
| `page_size` | Page size |
| `after`, `before` | Cursor pagination |

Lookup by exact symbol:

```http
GET /v2/products/{symbol}
```

The returned product defines `id`, `symbol`, `contract_type`, lifecycle/trading state, settlement time, tick and contract sizes, margin factors, fees, leverage, underlying/quote/settlement assets, spot index, and product-specific settings.

## Option-chain API

```http
GET /v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols=BTC&expiry_date=08-08-2026
```

The current repository uses this ticker query to resolve calls and puts by strike. In Delta's documentation, “Get option chain” is this filtered `/v2/tickers` request; it is not a separate `/options/chain` route.

## Listing straddles and strangles

Delta India currently exposes exchange-listed straddles as `move_options`. On 2026-08-08, this query returned six live contracts:

```http
GET https://api.india.delta.exchange/v2/products?contract_types=move_options&states=live&page_size=100
```

Example symbols/descriptions observed:

```text
MV-BTC-64800-090826  BTC Straddle expiring on 9-8-2026
MV-BTC-65000-090826  BTC Straddle expiring on 9-8-2026
```

The catalogue and CCXT source also recognize `spreads`, but the India catalogue returned zero live `spreads` at that time. No distinct live `strangle` contract type was observed. Therefore:

- Yes, Delta provides the list through `/v2/products`.
- For listed straddles, filter `contract_types=move_options&states=live`.
- For potential exchange-listed multi-leg spreads/strangles, also probe `contract_types=spreads&states=live` and inspect each product's description/specification.
- Do not equate an arbitrary call+put pair with an exchange-listed combo. A combo exists only if it is returned as one product ID.
- Availability is environment- and time-dependent. An empty result means none are listed for that filter now, not that the API never supports the type.

## Discovery helper

```python
from decimal import Decimal
import requests

BASE = "https://api.india.delta.exchange"

def live_products(contract_type):
    params = {
        "contract_types": contract_type,
        "states": "live",
        "page_size": 100,
    }
    products = []
    while True:
        page = requests.get(f"{BASE}/v2/products", params=params, timeout=10).json()
        if not page.get("success"):
            raise RuntimeError(page)
        products.extend(page["result"])
        cursor = page.get("meta", {}).get("after")
        if not cursor:
            return products
        params["after"] = cursor

for product in live_products("move_options"):
    print(product["id"], product["symbol"], product["description"])
```

## Selection checklist

Before trading a discovered product, validate:

- `state == "live"`
- `trading_status == "operational"`
- settlement time has not passed
- expected underlying, quote, and settlement assets
- `tick_size`, `contract_value`, and unit currency
- position size limit and applicable leverage/margin fields
- maker/taker commission fields
- exact option strike/expiry or combo semantics
