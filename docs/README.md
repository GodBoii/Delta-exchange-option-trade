# Delta Exchange developer documentation

This directory is the project-oriented, modular reference for integrating Delta Exchange. It focuses on Delta Exchange India because this application uses `https://api.india.delta.exchange`, while calling out global and testnet differences where they matter.

Verified: **2026-08-08**. Exchange capabilities, product listings, limits, and deprecations can change; re-check the official documentation and live `/v2/products` response before trading.

## Start here

1. [Concepts, environments, and conventions](01-concepts-environments.md)
2. [Authentication and signing](02-authentication-signing.md)
3. [Reliability: pagination, limits, and errors](03-reliability-errors.md)
4. [Products and instrument discovery](04-products-instruments.md)
5. [REST endpoint index](05-rest-api.md)
6. [Orders, positions, and risk controls](06-trading-risk.md)
7. [WebSocket feed](07-websocket.md)
8. [Python `delta-rest-client`](08-python-client.md)
9. [CCXT](09-ccxt.md)
10. [Implementation recipes](10-recipes.md)
11. [This repository's Delta integration](11-project-integration.md)
12. [Change watch and migration notes](12-change-watch.md)
13. [Research questions and verified answers](questions-and-answers.md)
14. [Automated strategy system](14-automated-strategy-system.md)

## Source hierarchy

When sources disagree, use this order:

1. Live API behavior for the selected environment.
2. [Official Delta API documentation](https://docs.delta.exchange/).
3. [Official Swagger v2 specification](https://docs.delta.exchange/api/swagger_v2.json).
4. [Delta's Python REST client](https://pypi.org/project/delta-rest-client/).
5. [CCXT's Delta adapter](https://github.com/ccxt/ccxt/blob/master/ts/src/delta.ts) for CCXT-specific behavior.
6. The supplied [`delta-exchange-api-docs.md`](../delta-exchange-api-docs.md) snapshot.

The monolithic source snapshot is retained at the repository root for auditability. These files organize, clarify, and supplement it; they do not replace Delta's contract with API users.

## Safety boundary

- Use testnet first.
- Never commit API keys or secrets.
- Trading keys require IP whitelisting; use a static egress IP.
- Treat order acceptance as asynchronous state, not proof of a fill.
- Use idempotent `client_order_id` values and reconcile orders, fills, and positions after reconnects.
- A two-leg options strategy is not atomic unless it is a single exchange-listed product.

