import { expect, test } from "vitest";
import {
  exclusionText, observationLabel, pnlQuery, runStub, tradeQuery, verifyReportAsset, verifyReportStrategy, walletUnavailableText, winRateText
} from "../lib/reporting";
import type { CapitalObservation, TradeItem } from "../lib/app-types";

const trade: TradeItem = {
  runId: "run", name: "Short strangle", asset: "BTC", status: "completed", accountingState: "settled",
  exclusionReason: null, createdAt: "2026-09-01T00:00:00Z", entryAt: null, exitAt: "2026-09-01T02:00:00Z",
  entryExecutedAt: "2026-09-01T01:00:00Z", exitExecutedAt: "2026-09-01T02:00:00Z",
  activityAt: "2026-09-01T02:00:00Z", realizedPnl: "1.5", grossPnl: "1.7", exchangeFees: "0.2",
  capitalBudget: "50", walletTotalAtEntry: null, walletAvailableAtEntry: null, deletedByUserAt: null,
  capturedAt: "2026-09-01T02:00:01Z"
};

test("trade query omits the all-state filter and keeps the cursor", () => {
  expect(tradeQuery({ range: "30d", state: "all" })).toBe("range=30d&limit=25");
  expect(tradeQuery({ range: "all", state: "open", cursor: "abc", deleted: "only", limit: 10 }))
    .toBe("range=all&limit=10&state=open&deleted=only&cursor=abc");
});

test("P&L queries apply the same asset scope to totals and paginated trades", () => {
  expect(pnlQuery({ range: "30d", asset: "all" })).toBe("range=30d");
  expect(tradeQuery({ range: "30d", state: "settled", asset: "all" })).toBe("range=30d&limit=25&state=settled");
  for (const asset of ["BTC", "ETH"] satisfies ("BTC" | "ETH")[]) {
    expect(pnlQuery({ range: "7d", asset })).toBe(`range=7d&asset=${asset}`);
    expect(tradeQuery({ range: "7d", state: "settled", asset, cursor: "next-page", limit: 50 }))
      .toBe(`range=7d&limit=50&state=settled&asset=${asset}&cursor=next-page`);
  }
});

test("a backend that ignores an asset filter cannot display combined data as BTC or ETH", () => {
  expect(() => verifyReportAsset({}, "all")).not.toThrow();
  for (const asset of ["all", "BTC", "ETH"] satisfies ("all" | "BTC" | "ETH")[]) {
    expect(() => verifyReportAsset({ asset }, asset)).not.toThrow();
  }
  expect(() => verifyReportAsset({}, "BTC")).toThrow(/server did not return BTC/);
  expect(() => verifyReportAsset({ asset: "all" }, "ETH")).toThrow(/server did not return ETH/);
  expect(() => verifyReportAsset({ asset: "BTC" }, "ETH")).toThrow(/server did not return ETH/);
  expect(() => verifyReportAsset({ asset: "ETH" }, "all")).toThrow(/combined/);
});

test("win rate reads as missing, not zero, before anything settles", () => {
  expect(winRateText(null)).toBe("No settled runs");
  expect(winRateText(0.5)).toBe("50.0%");
});

test("strategy names are encoded exactly and preserved through trade pagination", () => {
  const strategy = "ETH / Short & ATM + profit";
  const summary = new URLSearchParams(pnlQuery({ range: "all", asset: "ETH", strategy }));
  const trades = new URLSearchParams(tradeQuery({ range: "all", state: "open", strategy, cursor: "page-2" }));
  expect(summary.get("strategy")).toBe(strategy);
  expect(trades.get("strategy")).toBe(strategy);
  expect(trades.get("cursor")).toBe("page-2");
});

test("ignored strategy filters cannot present combined results under a strategy name", () => {
  expect(() => verifyReportStrategy({}, null)).not.toThrow();
  expect(() => verifyReportStrategy({ strategy: "all" }, "all")).not.toThrow();
  expect(() => verifyReportStrategy({}, "Short call")).toThrow(/selected strategy/);
  expect(() => verifyReportStrategy({ strategy: "Short put" }, "Short call")).toThrow(/selected strategy/);
});

test("open positions explain that premium is not profit", () => {
  expect(exclusionText("position_open")).toMatch(/not profit/);
  expect(exclusionText(null)).toBeNull();
  expect(exclusionText("something_new")).toBe("Accounting is incomplete.");
});

test("run stub falls back to creation time when the schedule is missing", () => {
  expect(runStub(trade)).toMatchObject({ id: "run", entryAt: "2026-09-01T00:00:00Z", exitAt: "2026-09-01T02:00:00Z" });
});

test("allocation budgets are never labelled as a wallet balance", () => {
  const base: CapitalObservation = {
    kind: "run_allocation", source: "strategy_entry", observedAt: "2026-09-01T01:00:00Z", runId: "run",
    totalBalance: null, availableBalance: null, allocationMode: null, capitalAmount: null, allocatedBudget: "50"
  };
  expect(observationLabel(base)).toBe("Budget allocated to one run");
  expect(observationLabel({ ...base, kind: "wallet", totalBalance: "100" })).toBe("Wallet balance at run entry");
  expect(observationLabel({ ...base, kind: "wallet", source: "live_wallet" })).toBe("Wallet balance read from Delta");
});

test("wallet failures say the balance is unavailable instead of showing zero", () => {
  expect(walletUnavailableText({ state: "unavailable", reason: "delta_request_failed", observedAt: null })).toMatch(/unavailable/);
  expect(walletUnavailableText({ state: "not_connected" })).toMatch(/not connected/);
  expect(walletUnavailableText({ state: "live", totalBalance: "1", availableBalance: "1", observedAt: "x" })).toBeNull();
});
