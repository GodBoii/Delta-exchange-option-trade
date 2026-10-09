import { expect, test } from "vitest";
import type { TradeItem } from "../lib/app-types";
import { filterStrategyTrades, settlementHeatmaps, strategyCsv, strategyMetrics } from "../lib/strategy-analytics";

function trade(runId: string, pnl: string | null, overrides: Partial<TradeItem> = {}): TradeItem {
  return {
    runId, name: "Short strangle", asset: "BTC", status: "completed", accountingState: "settled",
    exclusionReason: null, createdAt: "2026-10-01T19:00:00Z", entryAt: null, exitAt: null, entryExecutedAt: null,
    exitExecutedAt: "2026-10-01T19:00:00Z", activityAt: "2026-10-01T19:00:00Z", realizedPnl: pnl,
    grossPnl: null, exchangeFees: "0.5", capitalBudget: null, walletTotalAtEntry: null,
    walletAvailableAtEntry: null, deletedByUserAt: null, capturedAt: "2026-10-01T19:00:00Z", ...overrides,
  };
}

test("BTC includes legacy null-asset records, ETH stays separate, and strategy names match exactly", () => {
  const rows = [trade("old", "2", { asset: null }), trade("btc", "3"), trade("eth", "4", { asset: "ETH" }),
    trade("version", "5", { name: "Short strangle v2" })];
  const btc = filterStrategyTrades(rows, "BTC", "Short strangle");
  expect(btc.map(row => row.runId)).toEqual(["old", "btc"]);
  expect(btc.every(row => row.asset === "BTC")).toBe(true);
  expect(filterStrategyTrades(rows, "ETH", null).map(row => row.runId)).toEqual(["eth"]);
  expect(filterStrategyTrades(rows, "all", null)).toHaveLength(4);
});

test("metrics retain deleted owner history and exclude open and invalid results", () => {
  const metrics = strategyMetrics([trade("win", "10", { deletedByUserAt: "2026-10-02T00:00:00Z" }),
    trade("loss", "-4"), trade("even", "0", { exchangeFees: null }),
    trade("open", "99", { accountingState: "open" }), trade("invalid", "bad")]);
  expect(metrics).toMatchObject({ net: 6, wins: 1, lost: 1, even: 1, settled: 3, excluded: 2,
    fees: 1, missingFees: 1, profitFactor: 2.5, average: 2 });
  expect(metrics.winRate).toBeCloseTo(1 / 3);
});

test("drawdown starts at zero and measures money lost from the running peak", () => {
  const metrics = strategyMetrics([trade("a", "-3"), trade("b", "10", { exitExecutedAt: "2026-10-02T00:00:00Z" }),
    trade("c", "-5", { exitExecutedAt: "2026-10-03T00:00:00Z" })]);
  expect(metrics.drawdown.map(point => point.cumulative)).toEqual([-3, 0, -5]);
  expect(metrics.maxDrawdown).toBe(5);
  expect(strategyMetrics([])).toMatchObject({ winRate: null, average: null, profitFactor: null, maxDrawdown: 0 });
});

test("heatmaps place UTC evening settlements on the next IST date and distinguish zero from empty", () => {
  const { days, hours } = settlementHeatmaps(strategyMetrics([trade("a", "3"), trade("b", "-3")]).points);
  expect(days.get("2026-10-02")).toEqual({ net: 0, runs: 2 });
  expect(days.has("2026-10-01")).toBe(false);
  expect(hours[4][0]).toEqual({ net: 0, runs: 2 });
  expect(hours[4][1]).toEqual({ net: 0, runs: 0 });
});

test("CSV escapes names and blocks spreadsheet formulas", () => {
  const csv = strategyCsv([trade("a", "-3", { name: '=HYPERLINK("unsafe")' })]);
  expect(csv).toContain('"\'=HYPERLINK(""unsafe"")"');
  expect(csv).toContain('"Net P&L USD"');
  expect(csv).toContain('"-3"');
  expect(csv.split("\r\n")).toHaveLength(2);
});
