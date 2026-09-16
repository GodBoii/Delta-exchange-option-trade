/// <reference types="vite/client" />
import { convexTest } from "convex-test";
import { makeFunctionReference } from "convex/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import schema from "./schema";
import { sharedUserId } from "./sharedAnalysis";

const modules = import.meta.glob(["./**/*.ts", "./**/*.js", "!./**/*.test.ts"]);
const write = makeFunctionReference<"mutation">("runtimeRecords:write");
const publish = makeFunctionReference<"mutation">("sharedAnalysis:publish");
const allocate = makeFunctionReference<"mutation">("sharedAnalysis:allocate");
const recheck = makeFunctionReference<"mutation">("runtimeAutomation:recheck");
const pending = makeFunctionReference<"query">("sharedAnalysis:pendingAllocations");
beforeEach(() => { vi.stubEnv("CONVEX_TRADING_SECRET", "trade"); vi.stubEnv("CONVEX_RESEARCH_SECRET", "research"); });
afterEach(() => vi.unstubAllEnvs());

async function fixture() {
  const t = convexTest(schema, modules);
  const activation = new Date(Date.now() + 600000).toISOString();
  const exit = new Date(Date.now() + 3600000).toISOString();
  const definition = { name: "Common strategy", stopLossPercent: 100, entry: { entryAt: activation, exitAt: exit },
    lotsMode: "auto", legs: [{ id: "call", position: "sell", expiry: "2026-09-16" }] };
  await t.run(async ctx => {
    await ctx.db.insert("savedStrategies", { id: "saved", user_id: null, name: definition.name,
      definitionJson: JSON.stringify(definition), source_run_id: null, version: 1, enabled_for_ai: true,
      created_at: "date", updated_at: "date", deleted: false });
    for (const user of ["a", "b", "c"]) await ctx.db.insert("exchangeConnections", { id: user, user_id: user,
      delta_user_id: `delta-${user}`, account_name: user, email_masked: null, environment: "production",
      status: "connected", ciphertext: "test", fingerprint: user, updated_at: "date" });
  });
  const rows = [
    ["automation_agent_runs", { id: "run", user_id: sharedUserId, status: "running", market_snapshot_id: "snapshot" }],
    ["automation_market_snapshots", { id: "snapshot", user_id: sharedUserId, market_json: {} }],
    ...["a", "b", "c"].map(user => ["automation_settings", { id: user, user_id: user, enabled: true }]),
  ];
  for (const [table, row] of rows) await t.mutation(write, { secret: "trade", table, rowJson: JSON.stringify(row) });
  const input = { secret: "research", runId: "run", candidates: [{ id: "saved", version: 1 }], activation, expiry: exit,
    exit, confidence: .7, reasoning: "Range", supporting: [], invalidation: [], snapshotId: "snapshot", definitionJson: JSON.stringify(definition) };
  return { t, input, definition };
}

test("one decision and recheck allocate once to three accounts with the same schedule", async () => {
  const { t, input } = await fixture();
  const decision = await t.mutation(publish, input);
  const args = { secret: "trade", decisionId: decision.proposalId, userId: "a" };
  await expect(t.mutation(allocate, args)).rejects.toThrow("not confirmed");
  await t.run(async ctx => {
    const run = await ctx.db.query("automation_agent_runs").withIndex("by_external", q => q.eq("externalId", decision.activationRecheckRunId)).unique();
    if (!run) throw new Error("Missing recheck");
    await ctx.db.patch(run._id, { status: "running", rowJson: JSON.stringify({ ...JSON.parse(run.rowJson), status: "running" }) });
  });
  expect(await t.mutation(recheck, { secret: "research", userId: sharedUserId, runId: decision.activationRecheckRunId,
    proposalId: decision.proposalId, drop: false })).toMatchObject({ outcome: "strategy_reconfirmed" });
  // An outcome alone is insufficient until the backend records completion.
  await expect(t.mutation(allocate, args)).rejects.toThrow("not confirmed");
  await t.run(async ctx => {
    const run = await ctx.db.query("automation_agent_runs").withIndex("by_external", q => q.eq("externalId", decision.activationRecheckRunId)).unique();
    if (!run) throw new Error("Missing recheck");
    await ctx.db.patch(run._id, { status: "completed", rowJson: JSON.stringify({ ...JSON.parse(run.rowJson), status: "completed" }) });
  });
  expect(await t.query(pending, { secret: "trade" })).toHaveLength(3);
  for (const userId of ["a", "b", "c"]) {
    const first = await t.mutation(allocate, { ...args, userId });
    const second = await t.mutation(allocate, { ...args, userId });
    expect(second).toEqual({ strategyId: first.strategyId, reused: true });
  }
  expect(await t.query(pending, { secret: "trade" })).toEqual([]);
  const strategies = await t.run(ctx => ctx.db.query("strategies").collect());
  expect(strategies).toHaveLength(3);
  expect(new Set(strategies.map(row => row.owner))).toEqual(new Set(["a", "b", "c"]));
  for (const strategy of strategies) expect(JSON.parse(strategy.rowJson)).toMatchObject({ entry_at: input.activation, shared_decision_id: decision.proposalId });
});

test("shared publication rejects private data, changed risk, and multiple strategies", async () => {
  const { t, input, definition } = await fixture();
  await expect(t.mutation(publish, { ...input, snapshotId: "missing" })).rejects.toThrow("ownership");
  await expect(t.mutation(publish, { ...input, definitionJson: JSON.stringify({ ...definition, stopLossPercent: 50 }) })).rejects.toThrow("strategy-owned");
  await expect(t.mutation(publish, { ...input, candidates: [...input.candidates, { id: "other", version: 1 }] })).rejects.toThrow("candidates");
  await expect(t.mutation(publish, { ...input, confidence: 2 })).rejects.toThrow("Invalid shared");
  await t.mutation(publish, input);
  await expect(t.mutation(publish, input)).rejects.toThrow("another action");
});

test.each(["disabled", "disconnected", "changed", "dropped", "expired"])("allocation rejects %s state", async state => {
  const { t, input } = await fixture();
  const decision = await t.mutation(publish, input);
  await t.run(async ctx => {
    const run = await ctx.db.query("automation_agent_runs").withIndex("by_external", q => q.eq("externalId", decision.activationRecheckRunId)).unique();
    if (!run) throw new Error("Missing recheck");
    await ctx.db.patch(run._id, { status: "completed", rowJson: JSON.stringify({ ...JSON.parse(run.rowJson), status: "completed",
      outcome: state === "dropped" ? "strategy_dropped" : "strategy_reconfirmed" }) });
    if (state === "disabled") {
      const row = await ctx.db.query("automation_settings").withIndex("by_external", q => q.eq("externalId", "a")).unique();
      if (!row) throw new Error("Missing account");
      await ctx.db.patch(row._id, { rowJson: JSON.stringify({ ...JSON.parse(row.rowJson), enabled: false }) });
    }
    if (state === "disconnected") {
      const row = await ctx.db.query("exchangeConnections").withIndex("by_user", q => q.eq("user_id", "a")).unique();
      if (!row) throw new Error("Missing connection");
      await ctx.db.delete(row._id);
    }
    if (state === "changed") {
      const row = await ctx.db.query("savedStrategies").first();
      if (!row) throw new Error("Missing catalog");
      await ctx.db.patch(row._id, { version: 2 });
    }
    if (state === "expired") {
      const row = await ctx.db.query("strategy_proposals").withIndex("by_external", q => q.eq("externalId", decision.proposalId)).unique();
      if (!row) throw new Error("Missing proposal");
      await ctx.db.patch(row._id, { rowJson: JSON.stringify({ ...JSON.parse(row.rowJson), activation_time: new Date(0).toISOString() }) });
    }
  });
  await expect(t.mutation(allocate, { secret: "trade", decisionId: decision.proposalId, userId: "a" })).rejects.toThrow();
  expect(await t.run(ctx => ctx.db.query("strategies").collect())).toEqual([]);
});

test("manual requests from different users reuse one immediate shared analysis", async () => {
  const { t } = await fixture();
  await t.run(async ctx => {
    const old = await ctx.db.query("automation_agent_runs").first();
    if (!old) throw new Error("Missing old run");
    await ctx.db.patch(old._id, { status: "completed", rowJson: JSON.stringify({ ...JSON.parse(old.rowJson), status: "completed" }) });
  });
  await t.mutation(write, { secret: "trade", table: "automation_agent_runs", rowJson: JSON.stringify({
    id: "future-fixed", user_id: sharedUserId, status: "scheduled", created_at: new Date().toISOString(),
    scheduled_for: new Date(Date.now() + 3600000).toISOString(), run_key: "future-fixed",
  }) });
  const manual = makeFunctionReference<"mutation">("sharedAnalysis:manual");
  const a = await t.mutation(manual, { secret: "trade", requestedBy: "a" });
  const b = await t.mutation(manual, { secret: "trade", requestedBy: "b" });
  expect(a.id).toBe(b.id);
  expect(a.user_id).toBe(sharedUserId);
  expect(a.id).not.toBe("future-fixed");
  expect(a.status).toBe("scheduled");
  await expect(t.mutation(manual, { secret: "research", requestedBy: "a" })).rejects.toThrow();
});
