# Concepts, environments, and conventions

## Core objects

- **Asset**: currency used as an underlying, quote, or settlement currency.
- **Product**: a listed tradable contract. A product has a numeric `id` and a string `symbol`.
- **Underlying asset**: the asset on which a derivative is defined, such as BTC.
- **Quoting asset**: the unit in which the contract price is expressed.
- **Settling asset**: the unit in which margin and P&L are realized.
- **Index price**: a reference price derived from constituent spot markets.
- **Mark price**: the fair-price reference used for risk, liquidation, and optionally triggers.
- **Ticker**: current market snapshot including prices and product-specific metrics.

Orders use numeric `product_id`; market-data routes commonly use the product `symbol`. Do not persist a product ID without its environment and symbol: India, global, and testnet catalogues are separate.

## Environments

| Environment | REST | Private WebSocket | Public WebSocket |
|---|---|---|---|
| India production | `https://api.india.delta.exchange` | `wss://socket.india.delta.exchange` | `wss://public-socket.india.delta.exchange` |
| India testnet | `https://cdn-ind.testnet.deltaex.org` | `wss://socket-ind.testnet.deltaex.org` | `wss://socket-ind-pub.testnet.deltaex.org` |
| Global production | `https://api.delta.exchange` | Check global docs | Check global docs |
| Global testnet | `https://testnet-api.delta.exchange` | Check global docs | Check global docs |

This repository configures the India REST host. Never use a key created for one environment against another.

## Product types

Observed/documented contract types include:

- `perpetual_futures`
- `futures`
- `call_options`
- `put_options`
- `move_options`
- `spreads`
- `spot` and legacy/specialized types in global or historical catalogues

Do not hard-code this as a closed enum. Discover products and preserve unknown `contract_type` values for forward compatibility.

## Symbols

- Perpetual example: `BTCUSD`
- Call: `C-BTC-90000-310125`
- Put: `P-BTC-90000-310125`
- MOVE/straddle observed in India: `MV-BTC-64800-090826`
- Mark-price feed symbol: `MARK:BTCUSD`

Option date suffixes are `ddMMyy`. API expiry filters may use other documented formats, so do not derive request parameters solely by splitting a symbol.

## Data types

- REST timestamps are generally ISO 8601 strings; some request filters and WebSocket fields use Unix time in seconds, milliseconds, or microseconds. Confirm each field.
- Monetary values are often JSON strings. Parse with `Decimal`, not binary floating point, in order/risk code.
- IDs may exceed JavaScript's safe integer range. Treat IDs as strings unless arithmetic is required.
- Sizes are contract counts, not automatically asset quantities. Use `contract_value` and `contract_unit_currency`.

## Response envelope

Success:

```json
{
  "success": true,
  "result": {},
  "meta": { "after": null, "before": null }
}
```

Failure:

```json
{
  "success": false,
  "error": {
    "code": "invalid_contract",
    "context": {}
  }
}
```

Check both HTTP status and `success`; retain `error.code`, `error.context`, request ID headers, and a redacted request description in logs.

