import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { StrategyDefinition } from "../lib/strategy-types";

const calls = vi.hoisted(() => ({ query: vi.fn(), mutation: vi.fn(), auth: vi.fn(), session: vi.fn(), from: vi.fn() }));
vi.mock("convex/browser", () => ({ ConvexHttpClient: class {
  query = calls.query;
  mutation = calls.mutation;
  setAuth = calls.auth;
} }));
vi.mock("@/lib/supabase/client", () => ({ getSupabaseBrowserClient: () => ({ auth: { getSession: calls.session }, from: calls.from }) }));
beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_CONVEX_LIBRARY_ENABLED", "true");
  vi.stubEnv("NEXT_PUBLIC_CONVEX_URL", "https://library-test.convex.cloud");
  calls.session.mockResolvedValue({ data: { session: { access_token: "existing-login-token" } } });
});
afterEach(() => vi.unstubAllEnvs());

test("Convex library uses the existing login and never reads the retired database", async () => {
  const record = { id: "id", user_id: "owner", name: "Saved", definitionJson: '{"name":"Saved","legs":[]}',
    source_run_id: null, version: 3, enabled_for_ai: false, created_at: "date", updated_at: "date" };
  calls.query.mockImplementation(async (_ref, args) => ({ page: args.defaults ? [] : [record], isDone: true, continueCursor: "end" }));
  const { readStrategyLibrary } = await import("../lib/strategy-library");
  const rows = await readStrategyLibrary();
  expect(rows).toHaveLength(1);
  expect(rows[0].definition_json).toEqual({ name: "Saved", legs: [] });
  expect(rows[0].version).toBe(3);
  calls.from.mockImplementation(() => { throw new Error("Retired source must not be used"); });
  expect(calls.from).not.toHaveBeenCalled();
  expect(calls.auth).toHaveBeenCalledWith("existing-login-token");
});

test("Convex failures do not silently fall back to a second writable copy", async () => {
  calls.query.mockRejectedValue(new Error("Unavailable"));
  const { readStrategyLibrary } = await import("../lib/strategy-library");
  await expect(readStrategyLibrary()).rejects.toThrow("Unavailable");
  expect(calls.from).not.toHaveBeenCalled();
});

test("signed-out sessions cannot request the library", async () => {
  calls.session.mockResolvedValue({ data: { session: null } });
  const { readStrategyLibrary } = await import("../lib/strategy-library");
  await expect(readStrategyLibrary()).rejects.toThrow("Sign in");
  expect(calls.query).not.toHaveBeenCalled();
});

test("stored revision updates do not create another autosave", async () => {
  const { definitionFingerprint } = await import("../lib/strategy-library");
  const definition: StrategyDefinition = {
    schemaVersion: 2, version: 1, name: "Strategy", description: "", category: "premium_buying", marketOutlook: "bullish",
    enabledForAi: false, instrument: { index: "BTCUSD", underlying: "BTC", underlyingFrom: "cash" },
    entry: { strategyType: "intraday", entryAt: "2026-09-15T00:00:00Z", exitAt: "2026-09-15T01:00:00Z" },
    holdingMode: "intraday", expiryPolicy: "same_day", exitMinutesBeforeExpiry: 5, sameExpiryRequired: true,
    squareOff: "complete", riskMode: "strategy_level", riskBasis: "net_debit", stopLossPercent: 50, takeProfitPercent: 50,
    emergencyExitEnabled: false, trailToBreakEven: false, breakEvenScope: "all_legs", lotsMode: "manual",
    equalLotsRequired: false, acknowledgement: true, legs: [],
  };
  expect(definitionFingerprint(definition)).toBe(definitionFingerprint({ ...definition, version: 2 }));
  expect(definitionFingerprint(definition)).not.toBe(definitionFingerprint({ ...definition, name: "Changed" }));
});
