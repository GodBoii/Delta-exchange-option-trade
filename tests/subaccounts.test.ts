import { describe, expect, it } from "vitest";
import { accountState, mode, parseOverview, parsePendingRuns, parseServerIp, parseSnapshot, validTransferAmount } from "@/lib/subaccounts";

const overview = {
  mainAccount: { id: "100", name: "Main" },
  settings: { mode: "main", autoFund: false, revision: 0 },
  capacity: { available: 1, target: 4 },
  accounts: [{ id: "101", name: "Test", parentId: "100", marginMode: "cross", present: true,
    connected: true, busy: false, participation: "automatic", strategyId: null, strategyName: null, budget: null }],
  transfers: [{ id: "transfer-id", source_id: "100", target_id: "101", amount: "25.1234567890",
    state: "unknown", error: "Verify on Delta", created_at: "2026-10-17T00:00:00Z" }]
};

describe("Profile subaccounts", () => {
  it("does not label API access alone as ready to trade", () => {
    const account = parseOverview(overview).accounts[0];
    expect(accountState(account, null)).toBe("Connected; check account");
    const snapshot = parseSnapshot({ available: "0", balance: "0", positions: [], orders: [] });
    expect(accountState(account, snapshot)).toBe("Idle; funding needed");
    expect(accountState({ ...account, busy: true, strategyStatus: "active" }, snapshot)).toBe("Trading");
    expect(accountState({ ...account, busy: true, strategyStatus: "completed" }, snapshot)).toBe("Checking release");
    expect(accountState(account, snapshot, "Wallet unavailable")).toBe("Account check failed");
  });
  it("keeps the main-account default and uncertain transfer state", () => {
    const parsed = parseOverview(overview);
    expect(parsed.settings.mode).toBe("main");
    expect(parsed.transfers[0].state).toBe("unknown");
    expect(parsed.transfers[0].amount).toBe("25.1234567890");
  });
  it("rejects incomplete account responses", () => {
    expect(() => parseOverview({ ...overview, accounts: [{ id: "101" }] })).toThrow();
    expect(() => parseOverview({ ...overview, settings: { mode: "automatic" } })).toThrow();
    expect(() => mode("auto-create-accounts")).toThrow();
  });
  it("retains a stale IP address so it can be copied with its freshness warning", () => {
    expect(parseServerIp({ address: "2001:db8::1", checkedAt: null, stale: true })).toEqual({
      address: "2001:db8::1", checkedAt: null, stale: true
    });
  });
  it("preserves account-scoped product and order IDs", () => {
    const result = parseSnapshot({ balance: "100", available: "60", realizedPnl: "-2.5",
      positions: [{ product_id: 5, product_symbol: "C-BTC", size: -2, unrealized_pnl: "1.5" }],
      orders: [{ id: 8, product_id: 5, product_symbol: "C-BTC", side: "sell", size: "2" }] });
    expect(result.positions[0]).toMatchObject({ productId: 5, size: "-2", pnl: "1.5" });
    expect(result.orders[0]).toMatchObject({ id: 8, productId: 5 });
  });
  it("lists only unentered, unassigned runs for manual assignment", () => {
    expect(parsePendingRuns({ result: [
      { id: "a", name: "A", status: "scheduled" }, { id: "b", name: "B", status: "active" },
      { id: "c", name: "C", status: "draft", exchangeAccountId: "101" }
    ] })).toEqual([{ id: "a", name: "A" }]);
  });
  it.each(["0", "-1", "NaN", "Infinity", "1e3", "1.12345678901", "", ".5"])("rejects invalid transfer amount %s", value => {
    expect(validTransferAmount(value)).toBe(false);
  });
  it.each(["1", "0.5", "12345.1234567890"])("accepts a positive decimal amount %s", value => {
    expect(validTransferAmount(value)).toBe(true);
  });
});
