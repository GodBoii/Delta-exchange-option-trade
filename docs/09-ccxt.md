# CCXT integration

CCXT provides a unified exchange API and Delta-specific implicit methods. Delta describes CCXT as an authorized SDK provider.

## India configuration

CCXT's `delta` class defaults to the global REST host. Override both public and private URLs for India:

```python
import ccxt

exchange = ccxt.delta({
    "apiKey": "...",
    "secret": "...",
    "enableRateLimit": True,
    "urls": {
        "api": {
            "public": "https://api.india.delta.exchange",
            "private": "https://api.india.delta.exchange",
        }
    },
})
markets = exchange.load_markets()
```

For India testnet, either configure the explicit India testnet URLs or verify `set_sandbox_mode(True)` before use: CCXT's built-in testnet default is global and is not the India testnet host.

## Reported unified capabilities

The current CCXT adapter reports support for spot, swaps, and options; market loading; ticker(s), books, trades, OHLCV, option/Greeks, orders, balances, ledgers, positions, margin modification/mode, leverage, settlement history, status, and ADL rank. Several unified methods remain unsupported or partial, including funding history, option-chain aggregation, transfers/withdrawals, and some position-mode features.

Check at runtime:

```python
print(exchange.has)
```

Do not assume every `True` supports every Delta-specific parameter combination.

## Market symbols and product IDs

CCXT converts Delta products into unified symbols. Examples are conceptually:

```text
BTC/USD:USD                 # linear swap
BTC/USD:USD-260809-65000-C  # call option
BTC/USD:USD-260809-65000-P  # put option
BTC/USD:USD-260809-65000-M  # MOVE/straddle option
```

Use `exchange.market(unified_symbol)` to obtain the Delta `id`/numeric ID and raw `info`. Do not pass a Delta-native option symbol where a unified symbol is expected.

CCXT recognizes Delta `move_options` as an option with option type `move`; its source also acknowledges `spreads`, but generic parsing/strategy semantics for arbitrary spread products may be incomplete. Inspect raw `market["info"]`.

## Orders

```python
symbol = "BTC/USD:USD"
order = exchange.create_order(
    symbol,
    "limit",
    "buy",
    1,
    65000,
    {"client_order_id": "strategy01_leg01"},
)
```

Use unified methods where they preserve required semantics. Use an implicit Delta endpoint or raw REST only behind an adapter when a feature such as MMP or a new bracket field is not exposed cleanly.

## Important mismatches

- CCXT's source metadata can lag exchange documentation (for example legacy timeframes/order features).
- `fetchMarkets` makes one products call; confirm pagination behavior if the catalogue exceeds the returned page size.
- Unified option-chain support is reported false even though individual option markets and option data are supported.
- Rate limiting is local and cannot guarantee compliance with all Delta product-level quotas.
- Error normalization may discard Delta `context`; retain raw `info`/exception payload for operations.

Pin CCXT, record its version, and run testnet contract tests before upgrading.

