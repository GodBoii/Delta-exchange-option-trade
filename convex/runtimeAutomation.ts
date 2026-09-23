import { ConvexError, v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { authorizeAccountReader } from "./tradingAuth";
import { get, hydrate, ownerRows, parseRow, put, text, time } from "./runtimeRecords";
import { newRun, uuid } from "./runtimeControl";
import { validateMaterializedDefinition } from "./strategyDefinition";
import { sharedUserId } from "./sharedAnalysis";
import { getAutomation } from "./settings";
import { findUser } from "./userRecords";

export const context = query({
  args: { secret: v.string(), userId: v.string(), runId: v.string(), savedId: v.optional(v.string()) },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const run = await get(ctx, "analysisJobs", args.runId);
    if (!run || run.owner !== args.userId) throw new ConvexError("Agent run unavailable");
    const row = parseRow(run.rowJson);
    const settings = await getAutomation(ctx, args.userId);

    const parent = text(row, "parent_agent_run_id") ? await get(ctx, "analysisJobs", text(row, "parent_agent_run_id")) : null;
    const slots = await ownerRows(ctx, "strategy_capital_slots", args.userId);
    return { run: row, settings: settings,
      snapshot: null,
      parent: parent && parent.owner === args.userId ? await hydrate(ctx, parent) : null,
      occupied: slots.filter(slot => ["reserved", "active"].includes(slot.status)).length };
  },
});

export const saveSnapshot = mutation({
  args: { secret: v.string(), userId: v.string(), runId: v.string(), snapshotId: v.string() },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const run = await get(ctx, "analysisJobs", args.runId);
    if (!run || run.owner !== args.userId || run.status !== "running") throw new ConvexError("Run is not active");
    await put(ctx, "analysisJobs", { ...parseRow(run.rowJson), market_snapshot_id: args.snapshotId }, run);
    return args.snapshotId;
  },
});

export const schedule = mutation({
  args: { secret: v.string(), userId: v.string(), runId: v.string(), savedId: v.string(), savedVersion: v.number(),
    activation: v.string(), expiry: v.string(), exit: v.string(), recheck: v.string(), definitionJson: v.string(),
    confidence: v.number(), reasoning: v.string(), supporting: v.array(v.string()), invalidation: v.array(v.string()),
    snapshotId: v.string(), newsId: v.union(v.string(), v.null()) },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const run = await get(ctx, "analysisJobs", args.runId);
    const settings = await getAutomation(ctx, args.userId);
    if (!settings || !settings.enabled || !run || run.owner !== args.userId || run.status !== "running" || parseRow(run.rowJson).outcome) throw new ConvexError("Run cannot select another action");
    const saved = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", args.savedId)).unique();
    if (!saved || saved.deleted || !saved.enabled_for_ai || (saved.user_id !== null && saved.user_id !== args.userId) || saved.version !== args.savedVersion) throw new ConvexError("Saved strategy version unavailable");
    const source = parseRow(saved.definitionJson), materialized = parseRow(args.definitionJson);
    validateMaterializedDefinition(source, materialized);
    const connection = (await findUser(ctx, args.userId))?.connection;
    if (!connection || connection.status !== "connected") throw new ConvexError("Delta connection required");
    const capital = (await findUser(ctx, args.userId))?.capital;
    const mode = capital?.allocation_mode ?? "half_balance";
    const maximum = mode === "full_balance" ? 1 : mode === "half_balance" ? 2 : mode === "one_third_balance" ? 3 : mode === "one_quarter_balance" ? 4 : null;
    if (maximum !== null && (await ownerRows(ctx, "strategy_capital_slots", args.userId)).filter(slot => ["reserved", "active"].includes(slot.status)).length >= maximum) throw new ConvexError("No capital allocation available");
    if (time(args.recheck) <= Date.now() || time(args.activation) - time(args.recheck) !== 420000 || time(args.exit) <= time(args.activation)
        || time(args.expiry) <= time(args.activation) || args.confidence < 0 || args.confidence > 1) throw new ConvexError("Invalid strategy schedule");
    if (text(parseRow(run.rowJson), "market_snapshot_id") !== args.snapshotId) throw new ConvexError("Snapshot ownership mismatch");
    const strategyId = uuid(), proposalId = uuid(), recheckId = uuid();
    const now = new Date().toISOString();
    await put(ctx, "strategies", { id: strategyId, user_id: args.userId, saved_strategy_id: args.savedId, name: saved.name,
      status: "scheduled", definition_json: materialized, entry_at: args.activation, exit_at: args.exit,
      created_at: now, updated_at: now });
    await put(ctx, "strategy_proposals", { id: proposalId, user_id: args.userId, agent_run_id: args.runId,
      strategy_id: strategyId, saved_strategy_id: args.savedId, saved_strategy_version: args.savedVersion, status: "scheduled",
      activation_time: args.activation, proposal_expiry: args.expiry, ai_confidence: args.confidence, reasoning_summary: args.reasoning,
      supporting_signals: args.supporting, invalidation_signals: args.invalidation, market_snapshot_id: args.snapshotId, news_analysis_id: args.newsId, created_at: now });
    await put(ctx, "analysisJobs", newRun({ id: recheckId, user_id: args.userId, run_key: `activation-recheck:${proposalId}`,
      trigger: "activation_recheck", scheduled_for: args.recheck, reason: `Recheck ${saved.name} before activation`, strategy_proposal_id: proposalId }));
    await put(ctx, "analysisJobs", { ...parseRow(run.rowJson), outcome: "strategy_selected" }, run);
    return { outcome: "strategy_selected", proposalId, strategy: saved.name, strategyVersion: saved.version,
      activationTime: args.activation, proposalExpiry: args.expiry, scheduledStrategyId: strategyId,
      activationRecheckRunId: recheckId, activationRecheckTime: args.recheck, exitTime: args.exit, execution: "live_strategy_scheduler" };
  },
});

export const recheck = mutation({
  args: { secret: v.string(), userId: v.string(), runId: v.string(), proposalId: v.string(),
    drop: v.boolean(), name: v.optional(v.string()), activation: v.optional(v.string()), reason: v.optional(v.string()) },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const run = await get(ctx, "analysisJobs", args.runId);
    const proposal = await get(ctx, "strategy_proposals", args.proposalId);
    if (!run || run.owner !== args.userId || !proposal || proposal.owner !== args.userId || run.status !== "running") throw new ConvexError("Recheck unavailable");
    const state = parseRow(run.rowJson), proposed = parseRow(proposal.rowJson);
    if (state.trigger !== "activation_recheck" || state.strategy_proposal_id !== args.proposalId) throw new ConvexError("Recheck assignment mismatch");
    if (state.outcome) return { outcome: state.outcome };
    const strategy = await get(ctx, "strategies", text(proposed, "strategy_id"));
    const shared = args.userId === sharedUserId;
    const valid = proposal.status === "scheduled" && (shared || strategy?.status === "scheduled");
    const name = shared ? text(proposed, "name") : strategy ? text(parseRow(strategy.rowJson), "name") : "";
    if (args.drop && (!valid || name.toLowerCase() !== args.name?.trim().toLowerCase()
        || time(proposed.activation_time) !== time(args.activation) || !args.reason?.trim())) throw new ConvexError("Drop does not match assigned strategy");
    const outcome = args.drop || !valid ? "strategy_dropped" : "strategy_reconfirmed";
    if (args.drop) {
      const message = `Dropped by activation recheck: ${args.reason}`;
      if (strategy) await put(ctx, "strategies", { ...parseRow(strategy.rowJson), status: "cancelled", last_error: message }, strategy);
      await put(ctx, "strategy_proposals", { ...proposed, status: "cancelled", rejection_reason: message }, proposal);
    }
    await put(ctx, "analysisJobs", { ...state, outcome }, run);
    return { outcome, strategy: strategy ? text(parseRow(strategy.rowJson), "name") : null, activationTime: proposed.activation_time, reason: args.reason ?? null };
  },
});

export const followup = mutation({
  args: { secret: v.string(), userId: v.string(), runId: v.string(), next: v.string(), reason: v.string(), signals: v.array(v.string()),
    fixed: v.string(), previous: v.string(), dayStart: v.string(), dayEnd: v.string(), snapshotId: v.string(), newsId: v.union(v.string(), v.null()) },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const run = await get(ctx, "analysisJobs", args.runId);
    const config = await getAutomation(ctx, args.userId);
    if (!run || run.owner !== args.userId || run.status !== "running" || parseRow(run.rowJson).outcome || !config) throw new ConvexError("Run cannot choose another action");
    const state = parseRow(run.rowJson), settings = config;
    if (!settings.enabled || state.trigger === "agent_follow_up") throw new ConvexError("Follow-up chaining is disabled");
    if (!args.reason.trim() || time(args.next) < Date.now() + Number(settings.minimum_follow_up_minutes ?? 5) * 60000) throw new ConvexError("Follow-up is too soon");
    const records = await ctx.db.query("analysisJobs")
      .withIndex("by_owner_time", q => q.eq("owner", args.userId).gte("time", Math.min(time(args.previous), time(args.dayStart)))
        .lte("time", Math.max(time(args.fixed), time(args.next)))).take(1001);
    if (records.length > 1000) throw new ConvexError("Review window exceeds its bounded transaction size");
    const pending = records.filter(row => row.status === "scheduled" && text(parseRow(row.rowJson), "trigger") !== "activation_recheck" && row.time > Date.now()).sort((a, b) => a.time - b.time);
    let target = pending.find(row => row.time <= time(args.next));
    if (!target) {
      if (time(args.next) >= time(args.fixed)) throw new ConvexError("Follow-up must precede the next fixed review");
      target = pending.find(row => text(parseRow(row.rowJson), "trigger") === "agent_follow_up");
      if (!target && records.some(row => row.status !== "cancelled" && text(parseRow(row.rowJson), "trigger") === "agent_follow_up" && row.time > time(args.previous) && row.time < time(args.fixed))) throw new ConvexError("This review window already used its follow-up");
      if (!target && records.filter(row => text(parseRow(row.rowJson), "trigger") === "agent_follow_up" && row.status !== "cancelled" && row.time >= time(args.dayStart) && row.time < time(args.dayEnd)).length >= Number(settings.maximum_agent_runs_per_day ?? 3)) throw new ConvexError("Daily follow-up limit reached");
      const row = newRun({ ...(target ? parseRow(target.rowJson) : {}), user_id: args.userId, trigger: "agent_follow_up",
        run_key: `follow-up:${new Date(args.next).toISOString().slice(0, 16)}Z`, scheduled_for: args.next, reason: args.reason,
        signals_to_inspect: args.signals, market_snapshot_id: args.snapshotId, news_analysis_id: args.newsId, parent_agent_run_id: args.runId });
      await put(ctx, "analysisJobs", row, target);
      await put(ctx, "analysisJobs", { ...state, outcome: "wait_and_run_again" }, run);
      return { outcome: "wait_and_run_again", scheduledRunId: row.id, nextRunTime: args.next };
    }
    const row = parseRow(target.rowJson);
    if (!row.parent_agent_run_id) await put(ctx, "analysisJobs", { ...row, parent_agent_run_id: args.runId }, target);
    await put(ctx, "analysisJobs", { ...state, outcome: "wait_and_run_again" }, run);
    return { outcome: "wait_and_run_again", scheduledRunId: target.externalId, nextRunTime: row.scheduled_for, trigger: row.trigger, reusedExistingRun: true };
  },
});
