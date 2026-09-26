import { expect, test } from "vitest";
import {
  describeOrder, NO_PRICES, parseStreamMessage, positionView, positionViews, totalUnrealized
} from "../lib/portfolio";

const product = {
  symbol: "P-BTC-83600-270926", contract_value: "0.001", contract_type: "put_options", notional_type: "vanilla",
  strike_price: "83600", settlement_time: "2026-09-27T12:00:00Z", spot_index: { symbol: ".DEXBTUSD" },
  underlying_asset: { symbol: "BTC" }
};
// Figures from a live short put on Delta India.
const shortPut = {
  product_id: 154884, product_symbol: "P-BTC-83600-270926", size: -17, entry_price: "126.7", margin: "9.29",
  margin_mode: "cross", liquidation_price: null, mark_price: "123.17", realized_cashflow: "2.1539",
  realized_pnl: "0", unrealized_pnl: "2.142", product
};
const stopLoss = {
  id: 1564243452, product_id: 154884, side: "buy", size: 17, state: "pending", order_type: "market_order",
  stop_order_type: "stop_loss_order", stop_price: "333.647", stop_trigger_method: "mark_price", limit_price: null
};

test("short option P&L follows the live mark, not Delta's unsigned unrealized_pnl", () => {
  const view = positionView(shortPut, [stopLoss], { marks: { "P-BTC-83600-270926": 188 }, indices: { ".DEXBTUSD": 83849.1 } }, 0);
  expect(view.side).toBe("short");
  expect(view.units).toBeCloseTo(0.017, 10);
  expect(view.entryValue).toBeCloseTo(2.1539, 6);
  expect(view.unrealizedPnl).toBeCloseTo(-1.0421, 4);
  expect(view.unrealizedPercent).toBeCloseTo(-48.38, 2);
  expect(view.markSource).toBe("live");
  expect(view.notional).toBeCloseTo(1425.43, 2);
  // Delta showed 172.77x with -1.05 UPnL on 9.29 margin.
  expect(view.effectiveLeverage).toBeCloseTo(172.8, 1);
  expect(view.liquidation).toBeNull();
  expect(view.stopLoss).toMatchObject({ trigger: 333.647, limit: null, method: "mark_price" });
  expect(view.takeProfit).toBeNull();
});

test("without a live tick the snapshot mark is used and flagged", () => {
  const view = positionView(shortPut, [], NO_PRICES, 0);
  expect(view.markSource).toBe("snapshot");
  expect(view.unrealizedPnl).toBeCloseTo((123.17 - 126.7) * -17 * 0.001, 8);
  expect(view.index).toBeNull();
  expect(view.notional).toBeNull();
});

test("stops on the wrong side or another contract are not treated as protection", () => {
  const otherSide = { ...stopLoss, side: "sell" };
  const otherContract = { ...stopLoss, product_id: 1 };
  const view = positionView(shortPut, [otherSide, otherContract], NO_PRICES, 0);
  expect(view.stopLoss).toBeNull();
});

test("records without a product stay unpriced instead of guessing a contract size", () => {
  const view = positionView({ ...shortPut, product: null }, [], NO_PRICES, 0);
  expect(view.units).toBeNull();
  expect(view.unrealizedPnl).toBeNull();
  expect(totalUnrealized([view])).toBeNull();
});

test("flat rows are skipped and totals add priced legs", () => {
  const call = { ...shortPut, product_id: 154896, product_symbol: "C-BTC-84400-270926", entry_price: "145.5",
    mark_price: "146.6", product: { ...product, symbol: "C-BTC-84400-270926" } };
  const views = positionViews([shortPut, call, { ...shortPut, size: 0 }], [], NO_PRICES);
  expect(views).toHaveLength(2);
  expect(totalUnrealized(views)).toBeCloseTo((123.17 - 126.7) * -0.017 + (146.6 - 145.5) * -0.017, 8);
});

test("bracket stop orders are described as stops, not stuck market orders", () => {
  expect(describeOrder(stopLoss)).toMatchObject({ typeLabel: "Stop loss · market", protective: true, trigger: 333.647 });
  expect(describeOrder({ order_type: "limit_order", limit_price: "150" })).toMatchObject({
    typeLabel: "Limit Order", protective: false, limit: 150
  });
});

test("stream frames are validated at the boundary", () => {
  expect(parseStreamMessage("not json")).toBeNull();
  expect(parseStreamMessage('{"type":"state","positions":[],"orders":{},"balances":[]}')).toBeNull();
  expect(parseStreamMessage('{"type":"status","private":"live"}')).toEqual({ type: "status", private: "live" });
  const prices = parseStreamMessage('{"type":"prices","marks":{"A":"12.5","B":"x","C":"-1"},"indices":{},"at":5}');
  expect(prices).toEqual({ type: "prices", marks: { A: 12.5 }, indices: {}, at: 5 });
});
