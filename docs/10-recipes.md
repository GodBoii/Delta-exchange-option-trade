# Implementation recipes

## List every live product of a type

```python
import requests

def list_products(base_url, contract_types, states="live"):
    params = {
        "contract_types": contract_types,
        "states": states,
        "page_size": 100,
    }
    result = []
    while True:
        response = requests.get(
            f"{base_url}/v2/products", params=params, timeout=(3, 15)
        )
        response.raise_for_status()
        page = response.json()
        if not page.get("success"):
            raise RuntimeError(page.get("error"))
        result.extend(page["result"])
        after = page.get("meta", {}).get("after")
        if not after:
            return result
        params["after"] = after
```

```python
straddles = list_products(
    "https://api.india.delta.exchange", "move_options"
)
```

## Resolve an ATM call and put safely

1. Query live tickers/options for a specific underlying and expiry.
2. Split by `contract_type`.
3. Read the spot/index value returned by the API.
4. Choose the nearest listed strike independently for calls and puts.
5. Verify both products are live/operational and share the desired expiry/settlement asset.
6. Preview their books and margin.

Do not calculate an ATM strike from an assumed interval; strike grids can be irregular.

## Signed request with timeout reconciliation

```text
create intent with client_order_id
        |
sign and POST order
        |
    response known? -- yes --> store order --> follow fills/position
        |
        no
        |
GET by client_order_id --> found --> store/reconcile
        |
      absent
        |
check recent orders + fills + position --> decide whether safe to retry
```

An HTTP timeout is an unknown outcome, not an automatic failure.

## Combined-premium monitor

State required per strategy:

- actual filled quantity and average fill premium per leg
- product ID/symbol and contract value
- current authoritative mark/close price per leg
- aggregate entry credit, current close cost, realized fees/P&L
- trigger status and close-order IDs

Pseudo-code:

```python
entry_credit = sum(leg.entry_price * leg.contracts * leg.contract_value for leg in legs)
close_cost = sum(leg.mark_price * abs(leg.live_size) * leg.contract_value for leg in legs)
unrealized_loss = close_cost - entry_credit

if unrealized_loss >= entry_credit:  # 100% premium SL
    trigger_once()
    refresh_positions()
    close_each_leg_reduce_only()
    reconcile_until_flat_or_escalated()
```

This must be event-driven from WebSocket data with a stale-data guard. If any required price becomes stale or the socket disconnects, block new entries and use a defined fail-safe policy.

## WebSocket recovery

After reconnect:

1. Authenticate.
2. Subscribe to explicit products.
3. Fetch REST open orders, positions, and fills.
4. Recompute strategy state from exchange facts.
5. Fetch fresh book snapshots.
6. Resume decisions only when feeds and state are coherent.

## Pre-trade checklist

- Correct India/global/testnet host
- Clock synchronized
- Product live and operational
- Price/size quantized
- Sufficient margin with fee/slippage buffer
- Unique client order ID
- Known partial-leg failure policy
- WebSocket and REST reconciliation healthy
- Emergency per-leg protection and deadman switch configured
- Strategy monitor highly available for cross-leg rules

