import { describe, expect, it } from "vitest";
import { resolveLeg, strategySchema } from "@/lib/strategy";
import type { DeltaTicker } from "@/lib/delta";

const chain: DeltaTicker[] = [60000, 61000, 62000, 63000].flatMap((strike) => ([
  { product_id: strike, symbol: `C-BTC-${strike}-080826`, contract_type: "call_options", strike_price: String(strike), spot_price: "61600", mark_price: "100" },
  { product_id: strike + 1, symbol: `P-BTC-${strike}-080826`, contract_type: "put_options", strike_price: String(strike), spot_price: "61600", mark_price: "110" }
]));

const baseLeg = {
  id: "leg-1",
  lots: 2,
  position: "buy" as const,
  optionType: "call" as const,
  expiry: "2026-08-08",
  strikeMode: "atm" as const,
  strikeSteps: 0,
  orderType: "market_order" as const,
  reentryOnTarget: 0,
  reentryOnStop: 0
};

describe("strike resolution", () => {
  it("chooses the nearest ATM strike", () => {
    expect(resolveLeg(baseLeg, chain).strike).toBe(62000);
  });

  it("moves calls up for OTM and puts down for OTM", () => {
    expect(resolveLeg({ ...baseLeg, strikeMode: "otm", strikeSteps: 1 }, chain).strike).toBe(63000);
    expect(resolveLeg({ ...baseLeg, optionType: "put", strikeMode: "otm", strikeSteps: 1 }, chain).strike).toBe(61000);
  });

  it("resolves exact listed strikes", () => {
    expect(resolveLeg({ ...baseLeg, strikeMode: "exact", exactStrike: 60000 }, chain).productSymbol).toContain("60000");
  });
});

describe("strategy safety validation", () => {
  const strategy = {
    name: "BTC hedge",
    instrument: { index: "BTCUSD", underlying: "BTC", underlyingFrom: "cash" },
    entry: { strategyType: "intraday", entryAt: "2026-08-08T04:00:00.000Z", exitAt: "2026-08-08T10:00:00.000Z" },
    squareOff: "complete",
    trailToBreakEven: false,
    breakEvenScope: "all_legs",
    legs: [baseLeg],
    acknowledgement: true
  };

  it("accepts a valid strategy", () => expect(strategySchema.parse(strategy).legs).toHaveLength(1));

  it("rejects exit before entry", () => {
    expect(() => strategySchema.parse({ ...strategy, entry: { ...strategy.entry, exitAt: "2026-08-07T10:00:00.000Z" } })).toThrow();
  });

  it("requires stop loss for short options", () => {
    expect(() => strategySchema.parse({ ...strategy, legs: [{ ...baseLeg, position: "sell" }] })).toThrow();
  });
});
