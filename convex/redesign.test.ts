/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";
import { defaultAutomation } from "./userRecords";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const owner = "11111111-1111-4111-8111-111111111111";
const userId = (index: number) => "22222222-2222-4222-8222-" + index.toString().padStart(12, "0");
const fn = (name: string) => makeFunctionReference<"mutation">(name);
beforeEach(() => { vi.stubEnv("CONVEX_TRADING_SECRET", "trade"); vi.stubEnv("CONVEX_RESEARCH_SECRET", "research"); });
afterEach(() => vi.unstubAllEnvs());

async function setupSharedDecision(userCount: number) {
  const t = convexTest(schema, modules);
  const activation = new Date(Date.now() + 600000).toISOString();
  const exit = new Date(Date.now() + 3600000).toISOString();
  const definition = { name: "Shared", entry: { entryAt: activation, exitAt: exit },
    legs: [{ id: "call", position: "sell" }], lotsMode: "auto", stopLossPercent: 100 };
  await t.run(async ctx => {
    await ctx.db.insert("systemSettings", { key: "main", ownerUserId: owner, outboundIp: null,
      ipCheckedAt: null, analysis: { ...defaultAutomation, enabled: true } });
    await ctx.db.insert("savedStrategies", { id: "template", user_id: null, name: "Shared",
      definitionJson: JSON.stringify(definition), version: 1, enabled_for_ai: true, deleted: false,
      source_run_id: null, created_at: "date", updated_at: "date" });
    for (let i = 0; i < userCount; i++) await ctx.db.insert("users", {
      userId: userId(i), capital: { allocation_mode: "half_balance", capital_amount: null },
      automation: { ...defaultAutomation, enabled: true }, createdAt: "date", updatedAt: "date",
      connection: { id: userId(i), user_id: userId(i), delta_user_id: String(i), account_name: "Account",
        email_masked: null, environment: "production", status: "connected", ciphertext: "encrypted",
        fingerprint: String(i), updated_at: "date" },
    });
  });
  await t.mutation(fn("runtimeRecords:write"), { secret: "trade", table: "analysisJobs",
    rowJson: JSON.stringify({ id: "analysis", user_id: "global", status: "running", market_snapshot_id: "snapshot" }) });
  const decision = await t.mutation(fn("sharedAnalysis:publish"), { secret: "research", runId: "analysis",
    candidates: [{ id: "template", version: 1 }], activation, expiry: exit, exit, confidence: .8,
    reasoning: "Test market evidence", supporting: [], invalidation: [], snapshotId: "snapshot",
    definitionJson: JSON.stringify(definition) });
  await t.mutation(fn("runtimeRecords:update"), { secret: "trade", table: "analysisJobs",
    ids: [decision.activationRecheckRunId], conditions: [],
    patchJson: JSON.stringify({ status: "completed", outcome: "strategy_reconfirmed" }) });
  return { t, decision };
}

test("100 accounts receive one decision once, without duplicating analysis or reports", async () => {
  const { t, decision } = await setupSharedDecision(100);
  const pending = makeFunctionReference<"query">("sharedAnalysis:pendingAllocationPage");
  expect((await t.query(pending, { secret: "trade", cursor: null })).items).toHaveLength(100);
  for (let i = 0; i < 100; i++) {
    const args = { secret: "trade", decisionId: decision.proposalId, userId: userId(i) };
    const first = await t.mutation(fn("sharedAnalysis:allocate"), args);
    expect(await t.mutation(fn("sharedAnalysis:allocate"), args)).toEqual({ strategyId: first.strategyId, reused: true });
  }
  expect((await t.query(pending, { secret: "trade", cursor: null })).items).toEqual([]);
  await t.mutation(fn("sharedAnalysis:completeAllocation"), {
    secret: "trade", decisionId: decision.proposalId,
  });
  await t.run(async ctx => {
    const row = await ctx.db.query("strategy_proposals").withIndex("by_external",
      q => q.eq("externalId", decision.proposalId)).unique();
    expect(row && JSON.parse(row.rowJson).allocation_completed_at).toBeTruthy();
  });
  await t.run(async ctx => {
    expect(await ctx.db.query("strategies").collect()).toHaveLength(100);
    const runs = await ctx.db.query("analysisJobs").collect();
    expect(runs).toHaveLength(2);
    expect(runs.every(run => JSON.parse(run.rowJson).scope === "global")).toBe(true);
    expect(runs.every(run => !("user_id" in JSON.parse(run.rowJson)))).toBe(true);
  });
  const strategyIds = await t.run(async ctx => (await ctx.db.query("strategies").collect()).map(row => row.externalId));
  const states = await t.query(makeFunctionReference<"query">("runtimeControl:recheckStates"),
    { secret: "trade", strategyIds });
  expect(Object.keys(states)).toHaveLength(100);
  expect(Object.values(states).every(state => state === "ready")).toBe(true);
  await t.mutation(fn("accounts:provision"), { secret: "trade", userId: userId(101) });
  expect(await t.run(ctx => ctx.db.query("users").collect())).toHaveLength(101);
});

test("account allocation paginates beyond the first 100 users", async () => {
  const { t } = await setupSharedDecision(250);
  const pending = makeFunctionReference<"query">("sharedAnalysis:pendingAllocationPage");
  let cursor: string | null = null;
  const users = new Set<string>();
  const pageSizes = [];
  for (;;) {
    const page: { items: Array<{ userId: string }>; continueCursor: string; isDone: boolean } =
      await t.query(pending, { secret: "trade", cursor });
    pageSizes.push(page.items.length);
    for (const item of page.items) users.add(item.userId);
    if (page.isDone) break;
    cursor = page.continueCursor;
  }
  expect(pageSizes).toEqual([100, 100, 50]);
  expect(users.size).toBe(250);
});

test("owner saves a global built-in while ordinary users cannot change it", async () => {
  const t = convexTest(schema, modules);
  await t.run(ctx => ctx.db.insert("systemSettings", { key: "main", ownerUserId: owner,
    outboundIp: null, ipCheckedAt: null, analysis: { ...defaultAutomation, enabled: true } }));
  const id = "33333333-3333-4333-8333-333333333333";
  const draft = { id, name: "Built-in", definitionJson: JSON.stringify({
    name: "Built-in", enabledForAi: true, legs: [{ id: "call" }],
  }), enabled: true, expectedVersion: null };
  const saved = await t.withIdentity({ subject: owner }).mutation(fn("library:save"), draft);
  expect(saved.user_id).toBeNull();
  await expect(t.withIdentity({ subject: userId(1) }).mutation(fn("library:save"), {
    ...draft, expectedVersion: saved.version,
  })).rejects.toThrow("unavailable");
  await expect(t.mutation(fn("sharedAnalysis:manual"), {
    secret: "trade", requestedBy: userId(1),
  })).rejects.toThrow("Owner");
  const a = await t.mutation(fn("sharedAnalysis:manual"), { secret: "trade", requestedBy: owner });
  const b = await t.mutation(fn("sharedAnalysis:manual"), { secret: "trade", requestedBy: owner });
  expect(a.id).toBe(b.id);
});
