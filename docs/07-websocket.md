# WebSocket feed

Use WebSockets for market data and account events; use REST snapshots to initialize and recover.

## India endpoints

- Public: `wss://public-socket.india.delta.exchange`
- Private: `wss://socket.india.delta.exchange`
- Testnet public: `wss://socket-ind-pub.testnet.deltaex.org`
- Testnet private: `wss://socket-ind.testnet.deltaex.org`

The docs state a 60-second idle disconnect and 150 connection attempts per five minutes per IP.

## Subscribe and unsubscribe

```json
{
  "type": "subscribe",
  "payload": {
    "channels": [
      {"name": "ticker", "symbols": ["BTCUSD"]},
      {"name": "ob_l2", "symbols": ["BTCUSD"]},
      {"name": "trades", "symbols": ["BTCUSD"]}
    ]
  }
}
```

Use the same structure with `type: "unsubscribe"`. Omitting symbols unsubscribes the whole channel. `symbols: ["all"]` subscribes broadly, but Delta warns that snapshots are not sent for `all`; explicit symbols are safer for stateful books.

## Current public channels

| Channel | Use |
|---|---|
| `ticker` | Product price/market snapshot updates |
| `ob_l1` | Best bid/ask |
| `ob_l2` | L2 order-book snapshot |
| `ob_updates` | Incremental order-book changes |
| `trades` | Public trades |
| `mark_price` | Mark prices |
| `candlesticks` | OHLC updates |
| `spot_price` | Underlying index/spot prices |
| `spot_30mtwap_price` | 30-minute TWAP |
| `funding_rate` | Funding updates |
| `product_updates` | Product/listing changes |
| `system_status` | Maintenance and degraded-mode events |

Private channels include margins, positions, orders, user trades (`v2/user_trades`), portfolio margins, and MMP triggers.

## 2026 channel migration

The official changelog migrated public market data to the new public socket. Legacy names were scheduled for removal from the private endpoint on **2026-07-31**:

| Legacy | Current |
|---|---|
| `l1_orderbook` | `ob_l1` |
| `l2_orderbook` | `ob_l2` |
| `l2_updates` | `ob_updates` |
| `v2/ticker` | `ticker` |
| `all_trades` | `trades` |
| `v2/spot_price` | `spot_price` |

Do not copy the old subscription example from older snapshots unchanged.

## Authentication

Authenticate the private socket using `key-auth`, verify the explicit success response, then subscribe. Treat the legacy `auth` flow as removed.

## Book reconstruction

For an incremental L2 book:

1. Connect and subscribe to the explicit symbol.
2. Apply the initial snapshot.
3. Apply updates in documented sequence order.
4. Detect missing/out-of-order sequence values.
5. Discard local state and resubscribe/re-snapshot on a gap.

Never keep applying deltas after a detected gap.

## Liveness and reconnects

- Use the documented heartbeat channel/mechanism; ping/pong is an alternative where supported.
- Track time since last message and last successful heartbeat.
- Reconnect with capped exponential backoff plus jitter.
- Reauthenticate and resubscribe after reconnect.
- Fetch REST orders, positions, fills, and book snapshots to cover the disconnected interval.
- React to `system_status` maintenance/degraded messages by blocking unsafe new entries.

