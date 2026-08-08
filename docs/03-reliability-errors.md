# Reliability, pagination, limits, and errors

## Cursor pagination

The products, orders, order history, fills, and wallet transaction APIs use cursor pagination.

```python
params = {"page_size": 50}
while True:
    page = get_page(params)
    for item in page.get("result", []):
        consume(item)
    after = page.get("meta", {}).get("after")
    if not after:
        break
    params = {"page_size": 50, "after": after}
```

Do not assume a `total`/`total_count` field exists. Delta removed totals from order-history and fills pagination metadata in 2026. Cursors can change as new rows arrive; persist a business watermark such as fill timestamp plus fill ID for durable ingestion.

## Rate limits

Delta applies API-level and product-level quotas. The official order documentation currently describes 500 matching-engine operations per second for each product and says cancellations do not consume that product-operation limit; batch members count separately. Treat those figures as changeable. Use `GET /v2/rate_limits/quota` to observe current quota when available. The WebSocket documentation states 150 connection attempts per five minutes per IP and recommends waiting 5–10 minutes after a 429.

Client behavior:

- Pace requests locally; do not use retries as a rate limiter.
- On 429, honor `Retry-After` if present, otherwise exponential backoff with jitter.
- Retry GETs and safe reconciliation calls.
- Do not automatically retry an ambiguous order POST until checking by `client_order_id`.
- Spread subscriptions across a few long-lived sockets instead of reconnecting frequently.

## HTTP errors

| Status | Meaning | Response |
|---|---|---|
| 400 | invalid request | Correct parameters; do not blind-retry |
| 401 | bad credentials/signature | Recheck signing, key, environment, IP |
| 403 | CDN/policy rejection | Ensure `User-Agent`; inspect egress IP |
| 404 | unknown resource | Refresh product/order catalogue |
| 405/406 | method or format error | Correct request construction |
| 429 | quota exhausted | Back off with jitter |
| 500/503 | exchange failure/maintenance | Back off, check status, reconcile state |

Important order errors include `insufficient_margin`, `order_size_exceed_available`, `risk_limits_breached`, `invalid_contract`, `immediate_liquidation`, `out_of_bankruptcy`, `self_matching_disrupted_post_only`, and `immediate_execution_post_only`.

## Idempotency and reconciliation

Assign a unique `client_order_id` (current docs cap it at 32 characters) to every order intent. After a timeout:

1. Fetch by client order ID.
2. If absent, fetch recent open/closed orders and fills.
3. Compare positions.
4. Only then decide whether resubmission is safe.

For multi-leg strategies, persist each leg's intent and outcome independently. Sequential REST orders can partially execute.

## Numeric safety

Use `Decimal` for prices, fees, margin, premium, and P&L. Quantize price to `tick_size` and size to the product's permitted contract increment before signing the request.
