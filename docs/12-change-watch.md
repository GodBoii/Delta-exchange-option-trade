# Change watch and migration notes

## Current items requiring attention

### WebSocket migration deadline passed

Delta scheduled legacy public channels on the private India socket for removal on 2026-07-31. New implementations must use `wss://public-socket.india.delta.exchange` and current names such as `ticker`, `ob_l1`, `ob_l2`, `ob_updates`, and `trades`.

### WebSocket auth migration

The legacy `auth` method was scheduled to stop after 2025-12-31. Use `key-auth`.

### History pagination

Order history and fills no longer promise a total in metadata. The changelog also moved toward an effective maximum page size of 50 and limited comma-separated product IDs/symbols to ten.

### Limit-price validation

As of the 2026 change notes, `limit_price <= 0` is rejected. Omit it or use `null` where it is not required.

### Client-order ID

Current documentation caps `client_order_id` at 32 characters.

### Candle resolutions

Legacy `7d`, `2w`, and `30d` resolutions were scheduled to lose support in October 2025. Some SDK metadata may still advertise them; do not rely on that list without a test.

### FOK inconsistency

An older Delta changelog says FOK was removed in 2024, while older Python-client examples and current CCXT feature metadata may still mention it. Treat current live API/Swagger behavior as authoritative and avoid FOK until verified on testnet.

## Upgrade checklist

At least monthly and before every SDK upgrade:

1. Read Delta's API changelog.
2. Diff the Swagger specification.
3. Query live product types and important fields.
4. Verify REST and WebSocket hosts.
5. Verify active channel names and auth flow.
6. Run signing, market-data, order, cancel, and reconciliation tests on testnet.
7. Compare installed `delta-rest-client` and CCXT versions with pinned versions.
8. Update `Verified` date and record material findings here.

## Sources

- [Delta API documentation](https://docs.delta.exchange/)
- [Delta Swagger v2](https://docs.delta.exchange/api/swagger_v2.json)
- [Python REST client on PyPI](https://pypi.org/project/delta-rest-client/)
- [CCXT Delta TypeScript source](https://github.com/ccxt/ccxt/blob/master/ts/src/delta.ts)
- [CCXT manual](https://github.com/ccxt/ccxt/wiki/manual)

