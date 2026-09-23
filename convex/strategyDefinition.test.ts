import { expect, test } from "vitest";
import { validateMaterializedDefinition } from "./strategyDefinition";

const source = {
  holdingMode: "intraday", expiryPolicy: "next_day", takeProfitPercent: 80,
  emergencyStopLossPercent: 170, stopLossPercent: 100,
  entry: { strategyType: "intraday", entryAt: "2026-09-21T00:00:00Z", exitAt: "2026-09-21T07:00:00Z" },
  legs: [{ position: "sell", optionType: "put", orderType: "market_order", expiry: "2026-09-22" }],
};

test("agent can choose holding and expiry without changing risk or orders", () => {
  const proposed = { ...source, holdingMode: "hold_to_expiry", expiryPolicy: "7_day",
    entry: { ...source.entry, strategyType: "positional", exitAt: "2026-09-28T11:55:00Z" },
    legs: [{ ...source.legs[0], expiry: "2026-09-28" }],
  };
  expect(() => validateMaterializedDefinition(source, proposed)).not.toThrow();
  expect(() => validateMaterializedDefinition(source, { ...proposed, stopLossPercent: 50 })).toThrow("strategy-owned");
  expect(() => validateMaterializedDefinition(source, { ...proposed, legs: [{ ...proposed.legs[0], orderType: "limit_order" }] })).toThrow("leg configuration");
  expect(() => validateMaterializedDefinition(source, { ...proposed, holdingMode: "forever" })).toThrow("Invalid holding");
});
