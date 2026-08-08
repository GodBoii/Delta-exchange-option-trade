# REST API endpoint index

All paths below are under the selected REST base URL. Authentication means the signed headers in [Authentication and signing](02-authentication-signing.md).

## Public reference and market data

| Method | Path | Purpose |
|---|---|---|
| GET | `/v2/assets` | List assets |
| GET | `/v2/indices` | List price indices |
| GET | `/v2/products` | Paginated product catalogue |
| GET | `/v2/products/{symbol}` | Product by symbol |
| GET | `/v2/tickers` | Tickers, filters, and option-chain-style discovery |
| GET | `/v2/tickers/{symbol}` | One or comma-separated product symbols, subject to documented limits |
| GET | `/v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols=...&expiry_date=DD-MM-YYYY` | Option chain |
| GET | `/v2/l2orderbook/{symbol}` | L2 snapshot |
| GET | `/v2/trades/{symbol}` | Recent public trades |
| GET | `/v2/stats` | Volume statistics |
| GET | `/v2/history/candles` | Historical OHLC candles |
| GET | `/v2/history/sparklines` | Product sparkline history |
| GET | `/v2/products?states=expired` | Expired products and their settlement data |
| GET | `/v2/rate_limits/quota` | Current quota information |

## Orders (authenticated)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v2/orders` | Place order, optionally with bracket fields |
| PUT | `/v2/orders` | Edit order |
| DELETE | `/v2/orders` | Cancel order |
| GET | `/v2/orders` | Active orders with filters |
| GET | `/v2/orders/{order_id}` | Order by exchange ID |
| GET | `/v2/orders/client_order_id/{client_oid}` | Order by client ID |
| POST/PUT | `/v2/orders/bracket` | Place or edit bracket order |
| DELETE | `/v2/orders/all` | Cancel all matching open orders |
| POST/PUT/DELETE | `/v2/orders/batch` | Create, edit, or delete a batch |
| POST/GET | `/v2/products/{product_id}/orders/leverage` | Set/get per-product order leverage |

Consult current Swagger for exact batch maximum and field rules. Older Python package prose says five orders per batch; treat that as version-specific, not a permanent exchange guarantee.

## Positions (authenticated)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v2/positions/margined` | Margined positions, filterable by products |
| GET | `/v2/positions` | Position details |
| PUT | `/v2/positions/auto_topup` | Toggle auto top-up |
| POST | `/v2/positions/change_margin` | Add/remove position margin |
| POST | `/v2/positions/close_all` | Close all matching positions |

## History (authenticated)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v2/orders/history` | Closed/cancelled order history |
| GET | `/v2/fills` | User fills |
| GET | `/v2/fills/history/download/csv` | Download fills CSV |

History timestamps are commonly microseconds. The 2026 change notes cap product ID/symbol filters at ten and indicate a maximum effective history page size of 50; validate current behavior.

## Wallet and account (authenticated)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v2/wallet/balances` | Balances |
| GET | `/v2/wallet/transactions` | Wallet ledger |
| GET | `/v2/wallet/transactions/download` | Download wallet transactions |
| POST | `/v2/wallets/sub_account_balance_transfer` | Transfer between subaccounts |
| GET | `/v2/wallets/sub_accounts_transfer_history` | Transfer history |
| GET | `/v2/users/trading_preferences` | Trading preferences |
| PUT | `/v2/users/trading_preferences` | Update preferences |
| GET | `/v2/sub_accounts` | Subaccounts |
| GET | `/v2/profile` | User/profile |
| PUT | `/v2/users/margin_mode` | Change margin mode |

## Market-maker protection and safety

| Method | Path | Purpose |
|---|---|---|
| PUT | `/v2/users/update_mmp` | Update MMP configuration |
| PUT | `/v2/users/reset_mmp` | Reset MMP |
| POST | `/v2/heartbeat/create` | Create deadman-switch heartbeat |
| POST | `/v2/heartbeat` | Acknowledge/disable heartbeat |
| GET | `/v2/heartbeat` | Get heartbeats |

## Common order body

```json
{
  "product_id": 27,
  "size": 1,
  "side": "buy",
  "order_type": "limit_order",
  "limit_price": "65000.0",
  "time_in_force": "gtc",
  "post_only": false,
  "reduce_only": false,
  "client_order_id": "strategy01_leg01_abc123"
}
```

Omit `limit_price` or send `null` when it is not applicable. Delta's 2026 changelog states that values less than or equal to zero are rejected.
