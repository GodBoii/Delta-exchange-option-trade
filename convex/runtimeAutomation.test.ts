/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";
import { defaultAutomation } from "./userRecords";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const write = makeFunctionReference<"mutation">("runtimeRecords:write");
const schedule = makeFunctionReference<"mutation">("runtimeAutomation:schedule");
const context = makeFunctionReference<"query">("runtimeAutomation:context");
const read = makeFunctionReference<"query">("runtimeRecords:select");
beforeEach(() => { vi.stubEnv("CONVEX_TRADING_SECRET", "trade"); vi.stubEnv("CONVEX_RESEARCH_SECRET", "research"); });
afterEach(() => vi.unstubAllEnvs());

test("strategy, proposal, recheck and terminal action commit together", async () => {
  const t = convexTest(schema, modules);
  const definition = { name: "Strategy", enabledForAi: true, stopLossPercent: 100, legs: [{ id: "call", position: "sell", expiry: "2026-09-15" }] };
  await t.run(async ctx => {
    await ctx.db.insert("savedStrategies", { id: "saved", user_id: null, name: "Strategy", definitionJson: JSON.stringify(definition),
      source_run_id: null, version: 1, enabled_for_ai: true, created_at: "date", updated_at: "date", deleted: false });
    await ctx.db.insert("users", { userId: "owner", capital: { allocation_mode: "half_balance", capital_amount: null }, automation: { ...defaultAutomation, enabled: true }, createdAt: "date", updatedAt: "date", connection: { id: "connection", user_id: "owner", delta_user_id: "delta", account_name: "Main",
      email_masked: null, environment: "production", status: "connected", ciphertext: "encrypted", fingerprint: "fp", updated_at: "date" } });
  });
  for (const [table, row] of [
    ["analysisJobs", { id: "run", user_id: "owner", status: "running", outcome: null, market_snapshot_id: "snapshot" }],
  ]) await t.mutation(write, { secret: "trade", table, rowJson: JSON.stringify(row) });
  const at = Date.now() + 600000;
  const input = { secret: "research", userId: "owner", runId: "run", savedId: "saved", savedVersion: 1,
    activation: new Date(at).toISOString(), recheck: new Date(at - 420000).toISOString(),
    expiry: new Date(at + 60000).toISOString(), exit: new Date(at + 3600000).toISOString(),
    definitionJson: JSON.stringify(definition), confidence: .7, reasoning: "Range", supporting: [], invalidation: [], snapshotId: "snapshot", newsId: null };
  await expect(t.mutation(schedule, { ...input, definitionJson: JSON.stringify({ ...definition, stopLossPercent: 50 }) })).rejects.toThrow("strategy-owned");
  expect((await t.query(context, { secret: "research", userId: "owner", runId: "run" })).run.outcome).toBeNull();
  const result = await t.mutation(schedule, input);
  expect(result.outcome).toBe("strategy_selected");
  await expect(t.mutation(schedule, input)).rejects.toThrow("another action");
  const rows = await t.query(read, { secret: "trade", table: "analysisJobs", conditions: [], paginationOpts: { numItems: 100, cursor: null } });
  expect(rows.page.map((row: string) => JSON.parse(row)).find((row: { id: string }) => row.id === result.activationRecheckRunId)).toMatchObject({ strategy_proposal_id: result.proposalId });
});
