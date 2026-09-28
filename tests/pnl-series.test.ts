import { expect, test } from "vitest";
import { bucketSizeFor, bucketStart, bucketize, niceTicks, settledSeries } from "../lib/pnl-series";
import type { TradeItem } from "../lib/app-types";

function trade(runId: string, realizedPnl: string | null, exitExecutedAt: string, overrides: Partial<TradeItem> = {}): TradeItem {
  return {
    runId, name: `Run ${runId}`, asset: "BTC", status: "completed", accountingState: "settled",
    exclusionReason: null, createdAt: exitExecutedAt, entryAt: null, exitAt: null, entryExecutedAt: null,
    exitExecutedAt, activityAt: exitExecutedAt, realizedPnl, grossPnl: null, exchangeFees: null,
    capitalBudget: null, walletTotalAtEntry: null, walletAvailableAtEntry: null, deletedByUserAt: null,
    capturedAt: exitExecutedAt, ...overrides
  };
}

test("series runs in settlement order and adds up to the settled net", () => {
  const series = settledSeries([
    trade("b", "-2", "2026-09-02T12:00:00Z"),
    trade("a", "5", "2026-09-01T12:00:00Z"),
    trade("c", "1.5", "2026-09-03T12:00:00Z")
  ]);
  expect(series.map(point => point.runId)).toEqual(["a", "b", "c"]);
  expect(series.map(point => point.cumulative)).toEqual([5, 3, 4.5]);
});

test("unsettled runs and unreadable P&L stay off the curve", () => {
  const series = settledSeries([
    trade("open", "9", "2026-09-01T12:00:00Z", { accountingState: "open" }),
    trade("missing", null, "2026-09-01T12:00:00Z"),
    trade("kept", "1", "2026-09-01T12:00:00Z")
  ]);
  expect(series.map(point => point.runId)).toEqual(["kept"]);
});

test("falls back to last activity when the exit fill time is missing", () => {
  const [point] = settledSeries([trade("a", "1", "2026-09-01T12:00:00Z", { exitExecutedAt: null })]);
  expect(point.at).toBe(Date.parse("2026-09-01T12:00:00Z"));
});

test("buckets split wins from losses and count break-even runs", () => {
  const series = settledSeries([
    trade("a", "5", "2026-09-01T09:00:00Z"),
    trade("b", "-2", "2026-09-01T15:00:00Z"),
    trade("c", "0", "2026-09-01T16:00:00Z"),
    trade("d", "3", "2026-09-04T12:00:00Z")
  ]);
  const [first, second] = bucketize(series, "day");
  expect(first).toMatchObject({ gains: 5, losses: -2, net: 3, wins: 1, lost: 1, runs: 3 });
  expect(second).toMatchObject({ gains: 3, losses: 0, net: 3, runs: 1 });
});

test("bucket size widens with the span of the data", () => {
  const at = (iso: string) => settledSeries([trade("x", "1", iso)])[0];
  expect(bucketSizeFor([at("2026-09-01T12:00:00Z"), at("2026-09-20T12:00:00Z")])).toBe("day");
  expect(bucketSizeFor([at("2026-05-01T12:00:00Z"), at("2026-09-01T12:00:00Z")])).toBe("week");
  expect(bucketSizeFor([at("2025-09-01T12:00:00Z"), at("2026-09-01T12:00:00Z")])).toBe("month");
});

test("weeks start on Monday", () => {
  const sunday = new Date(2026, 8, 27, 18).getTime();
  expect(new Date(bucketStart(sunday, "week")).getDay()).toBe(1);
  expect(new Date(bucketStart(sunday, "week")).getDate()).toBe(21);
});

test("axis ticks always include zero and cover the range", () => {
  const ticks = niceTicks(-3, 17);
  expect(ticks).toContain(0);
  expect(ticks[0]).toBeLessThanOrEqual(-3);
  expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(17);
  expect(niceTicks(0, 0)).toEqual([0]);
  expect(niceTicks(2, 8)[0]).toBe(0);
});
