# Orders, positions, and risk controls

## Order lifecycle

Order state and fill state are separate. A successful POST means the request was accepted, not necessarily filled. Track:

- exchange order ID and `client_order_id`
- requested size and `unfilled_size`
- order state
- individual fills, fees, and fill price
- resulting position size and average entry

For market orders, price can differ from the preview. For multiple option legs, REST calls execute independently and can leave partial exposure.

## Side and exit logic

`side` describes the order action. Use `reduce_only: true` for exits when supported so an over-sized or stale close cannot reverse exposure. Determine the close side from the live position, not the original intended entry.

## Bracket controls are product-level

Delta order fields such as `bracket_stop_loss_price`, `bracket_take_profit_price`, `bracket_trail_amount`, and `bracket_stop_trigger_method` attach to an order/position for one `product_id`. They do not natively aggregate the premium or P&L of two unrelated call and put products.

Consequently, a call order plus a put order with brackets has two independent stops. A “combined premium” stop requires one of:

1. A single exchange-listed combo product (one product ID), if the desired payoff is listed.
2. An application-side strategy monitor that calculates aggregate live value/P&L and then closes both legs.

## Combined-premium short-straddle stop

Let the filled entry premiums per contract be `C0` and `P0`, sizes `qC` and `qP`, and current mark prices `Ct` and `Pt`.

```text
entry_credit = C0*qC + P0*qP
current_cost_to_close = Ct*qC + Pt*qP
loss = current_cost_to_close - entry_credit
```

A “100% of combined entry premium” loss threshold triggers when:

```text
loss >= entry_credit
```

Equivalently, for equal sizes and ignoring fees:

```text
Ct + Pt >= 2 * (C0 + P0)
```

Fees, slippage, contract values, and unequal quantities must be included in production. Use actual fills for `C0` and `P0`, not preview mark prices.

On trigger:

1. Re-read both live positions.
2. Submit reduce-only close orders for both legs.
3. Continue reconciliation if one close fails or partially fills.
4. Cancel related open/bracket orders to prevent stale triggers.
5. Record the aggregate trigger snapshot and each close result.

This client-side monitor is not atomic. It must be highly available and should be paired with exchange-side emergency per-leg protection and the deadman switch.

## Margin modes

The account exposes margin mode controls such as isolated and portfolio/cross modes depending on eligibility. Changing margin mode can affect all positions and risk calculations. Read current profile/preferences and positions after any change.

## Deadman switch

The heartbeat API can cancel orders or apply configured protective actions when acknowledgements stop. It protects against a dead client; it does not implement strategy P&L logic.

Typical flow:

```json
POST /v2/heartbeat/create
{
  "heartbeat_id": "strategy-engine-a",
  "impact": "contracts",
  "contract_types": ["perpetual_futures", "call_options", "put_options"],
  "config": [{"action": "cancel_orders", "unhealthy_count": 1}]
}
```

Then send `POST /v2/heartbeat` with the same ID and a TTL before expiry. Send TTL `0` for a deliberate shutdown if documented behavior remains unchanged.

