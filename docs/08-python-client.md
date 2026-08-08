# Python `delta-rest-client`

The Delta-maintained PyPI wrapper offers a smaller, exchange-specific API than raw REST or CCXT.

## Install and initialize

```bash
pip install delta-rest-client
```

```python
from delta_rest_client import DeltaRestClient

client = DeltaRestClient(
    base_url="https://cdn-ind.testnet.deltaex.org",
    api_key="...",
    api_secret="...",
)
```

Choose the base URL explicitly. The PyPI page lists India/global production and testnet hosts; start on the matching testnet.

## Documented method coverage

| Method family | Examples |
|---|---|
| Reference | `get_assets()`, `get_product(product_id)` |
| Market data | `get_ticker(symbol)`, `get_l2_orderbook(product_id)` |
| Orders | `get_live_orders()`, place/stop/cancel methods |
| Batch | `batch_create(...)`, `batch_cancel(...)` |
| Leverage/positions | `set_leverage(...)`, `get_position(...)`, `change_position_margin(...)` |
| Wallet | `get_balances(asset_id)` |
| History | `order_history(...)`, `fills(...)` with cursors |

Names and accepted enum values have changed across releases. Inspect the installed version's signatures rather than relying on old snippets. In particular, older examples show FOK even though Delta's 2024 changelog said FOK order support was removed; the 2026 official REST docs/source must win.

## Version discipline

Pin and test a known version:

```text
delta-rest-client==1.0.14
```

Version 1.0.14 was the current PyPI release when this documentation was verified. Before upgrading:

1. Read the package changelog.
2. Run contract tests against testnet.
3. Confirm signing serialization and endpoint host.
4. Confirm order enums and batch limits.
5. Re-test timeout/reconciliation behavior.

## When to use it

Use the official client when you want thin Delta-specific method names and its coverage matches the application. Use raw REST when you need a newly released endpoint. Use CCXT when a unified multi-exchange interface is more important than immediate Delta feature coverage.

## Production wrapper pattern

Wrap the client behind your own interface and add:

- request timeouts
- structured error mapping
- decimal quantization
- local throttling
- unique client order IDs
- timeout reconciliation
- metrics and redacted audit logs

This avoids coupling strategy logic to a package method or enum that may change.

