import { beforeEach, expect, test, vi } from "vitest";
import type { SavedStrategy } from "../lib/app-types";
import type { StrategyDefinition } from "../lib/strategy-types";

const calls = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("@/lib/api", () => ({ requestJson: calls.request }));
beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
});

const definition: StrategyDefinition = {
  schemaVersion: 2, version: 1, name: "Strategy", description: "", category: "premium_buying", marketOutlook: "bullish",
  enabledForAi: false, instrument: { index: "BTCUSD", underlying: "BTC", underlyingFrom: "cash" },
  entry: { strategyType: "intraday", entryAt: "2026-09-15T00:00:00Z", exitAt: "2026-09-15T01:00:00Z" },
  holdingMode: "intraday", expiryPolicy: "same_day", exitMinutesBeforeExpiry: 5, sameExpiryRequired: true,
  squareOff: "complete", riskMode: "strategy_level", riskBasis: "net_debit", stopLossPercent: 50, takeProfitPercent: 50,
  emergencyExitEnabled: false, trailToBreakEven: false, breakEvenScope: "all_legs", lotsMode: "manual",
  equalLotsRequired: false, acknowledgement: true, legs: [],
};

test("the library is read from the trading backend with the signed-in session", async () => {
  const row = { id: "id", user_id: "owner", name: "Saved", definition_json: { name: "Saved", legs: [] },
    source_run_id: null, version: 3, enabled_for_ai: false, created_at: "date", updated_at: "date" };
  calls.request.mockResolvedValue({ result: [row] });
  const { readStrategyLibrary } = await import("../lib/strategy-library");
  expect(await readStrategyLibrary()).toEqual([row]);
  expect(calls.request).toHaveBeenCalledWith("/api/library");
});

test("backend failures surface instead of falling back to another copy", async () => {
  calls.request.mockRejectedValue(new Error("Unavailable"));
  const { readStrategyLibrary } = await import("../lib/strategy-library");
  await expect(readStrategyLibrary()).rejects.toThrow("Unavailable");
});

test("saving an owned strategy sends its expected version; built-ins are copied for other users", async () => {
  calls.request.mockResolvedValue({ result: {} });
  const { saveLibraryStrategy } = await import("../lib/strategy-library");
  const owned = { id: "owned-id", version: 4, isDefault: false } as SavedStrategy;
  await saveLibraryStrategy(definition, owned);
  const [ownedPath, ownedInit] = calls.request.mock.calls[0];
  expect(ownedPath).toBe("/api/library/owned-id");
  expect(JSON.parse(ownedInit.body)).toMatchObject({ id: "owned-id", expectedVersion: 4, name: "Strategy" });

  const builtIn = { id: "builtin-id", version: 2, isDefault: true } as SavedStrategy;
  await saveLibraryStrategy(definition, builtIn);
  const [copyPath, copyInit] = calls.request.mock.calls[1];
  expect(copyPath).not.toBe("/api/library/builtin-id");
  expect(JSON.parse(copyInit.body).expectedVersion).toBeNull();
});

test("built-in strategies cannot be deleted by other users", async () => {
  const { deleteLibraryStrategy } = await import("../lib/strategy-library");
  await expect(deleteLibraryStrategy({ id: "x", version: 1, isDefault: true } as SavedStrategy)).rejects.toThrow("Default");
  expect(calls.request).not.toHaveBeenCalled();
  await deleteLibraryStrategy({ id: "x", version: 1, isDefault: false } as SavedStrategy);
  expect(calls.request).toHaveBeenCalledWith("/api/library/x?expectedVersion=1", { method: "DELETE" });
});

test("stored revision updates do not create another autosave", async () => {
  const { definitionFingerprint } = await import("../lib/strategy-library");
  expect(definitionFingerprint(definition)).toBe(definitionFingerprint({ ...definition, version: 2 }));
  expect(definitionFingerprint(definition)).not.toBe(definitionFingerprint({ ...definition, name: "Changed" }));
});
