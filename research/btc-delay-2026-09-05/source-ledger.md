# Research ledger

Access date September 5, 2026. Internal provenance; full raw market responses and checksum manifests are retained separately.

| Claim family | Source | Date and publisher | URL | Visible reference / access |
| --- | --- | --- | --- | --- |
| Published lag study and limits | Price Transmission from Bitcoin to Altcoins | Kurihara and Matsumoto; Springer Nature; March 10, 2026 | https://link.springer.com/article/10.1007/s10690-026-09589-z | turn49view0; full HTML inspected; long/flat equation verified |
| Meme spillovers change direction | Will memecoins' surge trigger a crypto crash? | Li and Yang; Finance Research Letters; 2022 | https://www.sciencedirect.com/science/article/pii/S154461232200397X | turn49search3 and prior turn43search1; publisher content |
| Archive schema/checksums | Binance Public Data | Binance; current repository | https://github.com/binance/binance-public-data | turn53view0; raw June/July archives verified |
| Delta instrument units | Products endpoint | Delta India; accessed 2026-09-05 | https://api.india.delta.exchange/v2/products | Direct public GET; delta-snapshot.json |
| Current quotes/liquidity | Perpetual tickers | Delta India; snapshot 03:47:48 UTC | https://api.india.delta.exchange/v2/tickers?contract_types=perpetual_futures | Direct public GET; delta-snapshot.json |
| Historical response | Historical candles | Delta India; July 2026 queried September 5 | https://api.india.delta.exchange/v2/history/candles | 150 exact URLs in delta-manifest.json; no authenticated requests |
| Protocol, id uniqueness, hosting, profile deprecation | API reference | Delta India; changelog through August 31, 2026 | https://docs.delta.exchange/ | turn60view0, turn61view1, turn61view2, turn61view3; sections inspected |
| Fees/GST | Fee schedule | Delta India; current | https://www.delta.exchange/fees | turn60view1 |
| Optional closing fee waiver | Scalper offer | Delta India; current | https://www.delta.exchange/support/solutions?articleId=80001172745 | turn60view3 |
| Funding | Funding mechanism | Delta India; current mechanism since September 8, 2025 | https://www.delta.exchange/support/solutions?articleId=80001199725 | Research agent official-source inspection; future product-specific accounting remains required |
| Binance stream routing and cadence | USD-M public/market streams | Binance; current | https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public | turn62view1; market companion turn62view2 |
| Binance migration | Important WebSocket Change Notice | Binance; current | https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice | turn60view2 |
| MCP scope | Tool overview | Delta; current | https://mcp.delta.exchange/docs/tools | turn62view0 |
| Multiple testing | The Deflated Sharpe Ratio | Bailey and Lopez de Prado; 2014 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 | prior turn37search0; DOI open failed this turn, prior abstract retained |

Gap matrix: current executable Delta lag unknown; literature cannot establish four meme winners. Candle differences measured, causal claim unproven. Historical spreads absent; current snapshot is only sensitivity input. Cost waiver eligibility unknown. Runtime latency unmeasured. Account integration reviewed statically, not exercised. No structural causality claim. No automated trade authorization.

Discovery used bounded BTC/meme lead-lag searches, the user's Delta catalog task, primary paper review, exchange protocol/fee references, code inspection and direct historical market data. Follow-up resolved scaled units, long/flat limitation, protocol migration, obsolete profile onboarding, unknown-order recovery, venue-specific candle differences and fee sensitivity. Stop reason: remaining material gaps require prospective executable quotes and later implementation, not additional broad literature searches. update_plan tool was not exposed in tool discovery; progress phases were tracked in commentary.
