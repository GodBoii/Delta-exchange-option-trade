import { ConvexError, v } from "convex/values";
import { mutation, query, type QueryCtx } from "./_generated/server";
import { get, parseRow, put, text, time } from "./runtimeRecords";
import { validateMaterializedDefinition } from "./strategyDefinition";
import { newRun, uuid } from "./runtimeControl";
import { authorizeAccountReader, authorizeTradingService } from "./tradingAuth";
import { getAutomation, globalScope } from "./settings";
import { findUser, requireOwner, systemConfig } from "./userRecords";

export const sharedUserId = globalScope;

export const publish = mutation({
  args: { secret: v.string(), runId: v.string(), candidates: v.array(v.object({ id: v.string(), version: v.number() })),
    activation: v.string(), expiry: v.string(), confidence: v.number(), reasoning: v.string(),
    supporting: v.array(v.string()), invalidation: v.array(v.string()), snapshotId: v.string(), definitionJson: v.string(), exit: v.string() },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const run = await get(ctx, "analysisJobs", args.runId);
    if (!run || run.owner !== sharedUserId || run.status !== "running" || parseRow(run.rowJson).outcome) throw new ConvexError("Shared run cannot choose another action");
    if (args.candidates.length !== 1 || new Set(args.candidates.map(item => item.id)).size !== args.candidates.length) throw new ConvexError("Invalid ranked candidates");
    if (time(args.activation) <= Date.now() + 420000 || time(args.expiry) <= time(args.activation)) throw new ConvexError("Invalid shared activation window");
    if (!Number.isFinite(args.confidence) || args.confidence < 0 || args.confidence > 1 || !args.reasoning.trim() || time(args.exit) <= time(args.activation)) throw new ConvexError("Invalid shared decision");
    if (text(parseRow(run.rowJson), "market_snapshot_id") !== args.snapshotId) throw new ConvexError("Snapshot ownership mismatch");
    const materialized = parseRow(args.definitionJson);
    const entry = parseRow(JSON.stringify(materialized.entry));
    if (time(entry.entryAt) !== time(args.activation) || time(entry.exitAt) !== time(args.exit)) throw new ConvexError("Definition schedule mismatch");
    const candidates = [];
    for (const candidate of args.candidates) {
      const saved = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", candidate.id)).unique();
      if (!saved || saved.user_id !== null || !saved.enabled_for_ai || saved.deleted || saved.version !== candidate.version) throw new ConvexError("Shared candidate is not a current public template");
      validateMaterializedDefinition(parseRow(saved.definitionJson), materialized);
      candidates.push({ id: saved.id, version: saved.version, name: saved.name, definition_json: parseRow(saved.definitionJson) });
    }
    const id = uuid(), recheckId = uuid();
    await put(ctx, "strategy_proposals", { id, user_id: sharedUserId, agent_run_id: args.runId, strategy_id: null,
      saved_strategy_id: args.candidates[0].id, saved_strategy_version: args.candidates[0].version, status: "scheduled",
      name: candidates[0].name, candidates, definition_json: materialized, exit_at: args.exit, activation_time: args.activation, proposal_expiry: args.expiry,
      ai_confidence: args.confidence, reasoning_summary: args.reasoning, supporting_signals: args.supporting,
      invalidation_signals: args.invalidation, market_snapshot_id: args.snapshotId, shared_recheck_run_id: recheckId,
      created_at: new Date().toISOString() });
    await put(ctx, "analysisJobs", newRun({ id: recheckId, user_id: sharedUserId, trigger: "activation_recheck",
      run_key: `shared-recheck:${id}`, scheduled_for: new Date(time(args.activation) - 420000).toISOString(),
      strategy_proposal_id: id, reason: "Recheck the shared ranked decision before account-specific execution" }));
    await put(ctx, "analysisJobs", { ...parseRow(run.rowJson), outcome: "strategy_selected", shared_decision_id: id }, run);
    return { outcome: "strategy_selected", proposalId: id, activationRecheckRunId: recheckId, activationTime: args.activation,
      strategy: "Shared ranked strategy decision", candidateCount: candidates.length, execution: "shared_analysis" };
  },
});

export const allocate = mutation({
  args: { secret: v.string(), decisionId: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const decision = await get(ctx, "strategy_proposals", args.decisionId);
    if (!decision || decision.owner !== sharedUserId || decision.status !== "scheduled") throw new ConvexError("Shared decision unavailable");
    const plan = parseRow(decision.rowJson);
    const recheck = await get(ctx, "analysisJobs", text(plan, "shared_recheck_run_id"));
    if (!recheck || recheck.status !== "completed" || parseRow(recheck.rowJson).outcome !== "strategy_reconfirmed") throw new ConvexError("Shared recheck has not confirmed entry");
    if (Date.now() >= time(plan.activation_time)) throw new ConvexError("Shared entry allocation window elapsed");
    const settings = await getAutomation(ctx, args.userId);
    if (!settings || !settings.enabled) throw new ConvexError("Account automation disabled");
    const saved = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", text(plan, "saved_strategy_id"))).unique();
    if (!saved || saved.deleted || !saved.enabled_for_ai || saved.version !== plan.saved_strategy_version || saved.user_id !== null) throw new ConvexError("Built-in strategy changed");
    if (!Array.isArray(plan.candidates) || !plan.candidates.some(candidate =>
      candidate !== null && typeof candidate === "object" && "id" in candidate && "version" in candidate &&
      candidate.id === saved.id && candidate.version === saved.version)) throw new ConvexError("Strategy was not ranked in the shared decision");
    const connection = (await findUser(ctx, args.userId))?.connection;
    if (!connection || connection.status !== "connected") throw new ConvexError("Delta connection required");
    const allocated = await ctx.db.query("strategy_proposals").withIndex("by_owner_unique", q =>
      q.eq("owner", args.userId).eq("uniqueKey", `shared:${args.decisionId}`)).unique();
    if (allocated) return { strategyId: parseRow(allocated.rowJson).strategy_id, reused: true };
    const id = uuid(), strategyId = uuid();
    await put(ctx, "strategies", { id: strategyId, user_id: args.userId, name: saved.name, saved_strategy_id: saved.id,
      status: "scheduled", definition_json: plan.definition_json, entry_at: plan.activation_time, exit_at: plan.exit_at,
      shared_decision_id: args.decisionId, created_at: new Date().toISOString() });
    await put(ctx, "strategy_proposals", { id, user_id: args.userId, agent_run_id: plan.agent_run_id, strategy_id: strategyId,
      shared_decision_id: args.decisionId, shared_recheck_run_id: plan.shared_recheck_run_id, saved_strategy_id: saved.id,
      saved_strategy_version: saved.version, status: "scheduled", activation_time: plan.activation_time, proposal_expiry: plan.proposal_expiry,
      ai_confidence: plan.ai_confidence, reasoning_summary: plan.reasoning_summary, supporting_signals: plan.supporting_signals,
      invalidation_signals: plan.invalidation_signals, market_snapshot_id: plan.market_snapshot_id, created_at: new Date().toISOString() });
    return { strategyId, reused: false };
  },
});

/** Account pages keep each query within Convex transaction limits. */
async function pendingAccountPage(ctx: QueryCtx, cursor: string | null) {
  const decisions = await ctx.db.query("strategy_proposals")
    .withIndex("by_owner_time", q => q.eq("owner", sharedUserId).gt("time", Date.now()))
    .filter(q => q.eq(q.field("status"), "scheduled")).take(101);
  if (decisions.length > 100) throw new ConvexError("Too many pending shared decisions");
  const ready = [];
    for (const decision of decisions) {
      const plan = parseRow(decision.rowJson);
      if (plan.allocation_completed_at) continue;
    if (time(plan.activation_time) <= Date.now()) continue;
    const recheck = await get(ctx, "analysisJobs", text(plan, "shared_recheck_run_id"));
    if (recheck?.status === "completed" && parseRow(recheck.rowJson).outcome === "strategy_reconfirmed") {
      ready.push(decision);
    }
  }
    if (!ready.length) return { items: [], decisions: [], continueCursor: "", isDone: true };
  const accounts = await ctx.db.query("users").withIndex("by_user")
    .paginate({ numItems: 100, cursor });
  const pending = [];
  for (const decision of ready) {
    for (const account of accounts.page) {
      if (!account.automation.enabled) continue;
      const existing = await ctx.db.query("strategy_proposals").withIndex("by_owner_unique", q =>
        q.eq("owner", account.userId).eq("uniqueKey", `shared:${decision.externalId}`)).unique();
      if (existing) continue;
      const connection = account.connection;
      if (connection?.status === "connected") pending.push({ decisionId: decision.externalId, userId: account.userId });
    }
  }
  return { items: pending, decisions: ready.map(decision => decision.externalId),
    continueCursor: accounts.continueCursor, isDone: accounts.isDone };
}

/** Compatibility while a previous backend process may still be running. */
export const pendingAllocations = query({
  args: { secret: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    return (await pendingAccountPage(ctx, null)).items;
  },
});

export const pendingAllocationPage = query({
  args: { secret: v.string(), cursor: v.union(v.string(), v.null()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    return pendingAccountPage(ctx, args.cursor);
  },
});

/** Stop rescanning every account after a complete successful allocation pass. */
export const completeAllocation = mutation({
  args: { secret: v.string(), decisionId: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const decision = await get(ctx, "strategy_proposals", args.decisionId);
    if (!decision || decision.owner !== sharedUserId || decision.status !== "scheduled") {
      throw new ConvexError("Shared decision unavailable");
    }
    const row = parseRow(decision.rowJson);
    if (row.allocation_completed_at) return;
    const recheck = await get(ctx, "analysisJobs", text(row, "shared_recheck_run_id"));
    if (!recheck || recheck.status !== "completed" || parseRow(recheck.rowJson).outcome !== "strategy_reconfirmed") {
      throw new ConvexError("Shared recheck has not confirmed entry");
    }
    await put(ctx, "strategy_proposals", { ...row, allocation_completed_at: new Date().toISOString() }, decision);
  },
});

export const manual = mutation({
  args: { secret: v.string(), requestedBy: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    await requireOwner(ctx, args.requestedBy);
    if (!(await systemConfig(ctx))?.analysis.enabled) throw new ConvexError("Global analysis is paused");
    const running = await ctx.db.query("analysisJobs")
      .withIndex("by_owner_status_created", q => q.eq("owner", sharedUserId).eq("status", "running")).collect();
    const pending = await ctx.db.query("analysisJobs")
      .withIndex("by_owner_time", q => q.eq("owner", sharedUserId).lte("time", Date.now())).collect();
    const existing = [...running, ...pending].find(item =>
      ["scheduled", "running"].includes(item.status) && parseRow(item.rowJson).trigger !== "activation_recheck");
    if (existing) return parseRow(existing.rowJson);
    const row = newRun({ user_id: sharedUserId, trigger: "manual", run_key: `shared-manual:${uuid()}`,
      scheduled_for: new Date().toISOString(), reason: "User requested a shared market analysis" });
    await put(ctx, "analysisJobs", row);
    return row;
  },
});
