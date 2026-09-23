import { ConvexError, v } from "convex/values";
import { mutation } from "./_generated/server";
import { get, ownerRows, parseRow, put, text, time, type Row } from "./runtimeRecords";
import { authorizeTradingService } from "./tradingAuth";
import { findUser } from "./userRecords";

const fixedReviewTriggers = new Set([
  "asia_session",
  "london_session",
  "new_york_session",
  "pre_expiry",
  "midnight_review",
]);

export function uuid() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, char => {
    const random = Math.floor(Math.random() * 16);
    return (char === "x" ? random : (random & 3) | 8).toString(16);
  });
}
export function newRun(value: Row): Row {
  const now = new Date().toISOString();
  return { id: uuid(), created_at: now, updated_at: now, status: "scheduled", outcome: null, parent_agent_run_id: null,
    model_id: "xiaomi/mimo-v2.6-pro", signals_to_inspect: [], ...value };
}

export const reserveCapital = mutation({
  args: { secret: v.string(), p_user_id: v.string(), p_strategy_id: v.string(), p_maximum_slots: v.number(),
    p_budget: v.string(), p_total_balance: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (!Number.isInteger(args.p_maximum_slots) || args.p_maximum_slots < 1 || args.p_maximum_slots > 100) throw new ConvexError("Invalid slot count");
    const strategy = await get(ctx, "strategies", args.p_strategy_id);
    if (!strategy || strategy.owner !== args.p_user_id) throw new ConvexError("Strategy unavailable");
    const slots = await ownerRows(ctx, "strategy_capital_slots", args.p_user_id);
    const active = slots.filter(slot => ["reserved", "active"].includes(slot.status));
    const existing = active.find(slot => slot.relation === args.p_strategy_id);
    if (existing) return { slot: parseRow(existing.rowJson).slot_number, created: false, occupiedBefore: active.length - 1 };
    const fixed = (value: unknown): bigint => {
      if (typeof value !== "string" || !/^[0-9]+(?:\.[0-9]{1,30})?$/.test(value) || value.length > 80) throw new ConvexError("Invalid reserved capital");
      const [whole, fraction = ""] = value.split(".");
      return BigInt(whole) * 10n ** 30n + BigInt(fraction.padEnd(30, "0"));
    };
    const connection = (await findUser(ctx, args.p_user_id))?.connection;
    if (!connection || connection.status !== "connected") throw new ConvexError("Delta account required");
    const aliases = await ctx.db.query("users").withIndex("by_delta_account", q => q.eq("connection.delta_user_id", connection.delta_user_id)).collect();
    let reserved = 0n;
    for (const alias of aliases) {
      for (const item of await ownerRows(ctx, "strategy_capital_slots", alias.userId)) {
        if (["reserved", "active"].includes(item.status)) reserved += fixed(parseRow(item.rowJson).reserved_budget);
      }
    }
    if (fixed(args.p_budget) <= 0n || reserved + fixed(args.p_budget) > fixed(args.p_total_balance)) {
      throw new ConvexError({ code: "capital_slots_full", message: "Account capital is already reserved" });
    }
    if (active.length >= args.p_maximum_slots) throw new ConvexError({ code: "capital_slots_full", message: "All capital allocations are occupied" });
    for (let slot = 1; slot <= args.p_maximum_slots; slot++) {
      const current = slots.find(row => parseRow(row.rowJson).slot_number === slot);
      if (current && current.status !== "available") continue;
      await put(ctx, "strategy_capital_slots", {
        id: current?.externalId ?? uuid(), user_id: args.p_user_id, slot_number: slot, strategy_id: args.p_strategy_id,
        proposal_id: null, status: "reserved", reserved_at: new Date().toISOString(), released_at: null,
        reserved_budget: args.p_budget,
      }, current);
      return { slot, created: true, occupiedBefore: active.length };
    }
    throw new ConvexError({ code: "capital_slots_full", message: "No eligible capital slot" });
  },
});

export const releaseCapital = mutation({
  args: { secret: v.string(), p_user_id: v.string(), p_strategy_id: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const slots = await ctx.db.query("strategy_capital_slots").withIndex("by_relation", q => q.eq("relation", args.p_strategy_id)).collect();
    for (const slot of slots) if (slot.owner === args.p_user_id) await put(ctx, "strategy_capital_slots", {
      ...parseRow(slot.rowJson), status: "available", strategy_id: null, proposal_id: null,
      reserved_at: null, released_at: new Date().toISOString(),
    }, slot);
    return true;
  },
});

export const claimAgent = mutation({
  args: { secret: v.string(), p_user_id: v.string(), p_run_id: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const run = await get(ctx, "analysisJobs", args.p_run_id);
    if (!run || run.owner !== args.p_user_id || run.status !== "scheduled") return [];
    const row = { ...parseRow(run.rowJson), status: "running", started_at: new Date().toISOString(), error: null };
    await put(ctx, "analysisJobs", row, run);
    return [row];
  },
});

// Deployment migration for pending schedules. Running/near-entry rechecks are left intact.
export const reschedulePendingRechecks = mutation({
  args: { secret: v.string(), cursor: v.union(v.string(), v.null()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const page = await ctx.db.query("analysisJobs")
      .withIndex("by_status_time", q => q.eq("status", "scheduled"))
      .paginate({ numItems: 100, cursor: args.cursor });
    let updated = 0;
    for (const run of page.page) {
      const row = parseRow(run.rowJson);
      if (row.trigger !== "activation_recheck") continue;
      const proposal = await get(ctx, "strategy_proposals", text(row, "strategy_proposal_id"));
      if (!proposal || proposal.owner !== run.owner) continue;
      const target = time(parseRow(proposal.rowJson).activation_time) - 420000;
      if (!Number.isFinite(target) || target <= Date.now() || run.time === target) continue;
      await put(ctx, "analysisJobs", { ...row, scheduled_for: new Date(target).toISOString() }, run);
      updated += 1;
    }
    return { updated, cursor: page.continueCursor, isDone: page.isDone };
  },
});

export const ensureFixedRuns = mutation({
  args: { secret: v.string(), p_runs: v.array(v.object({ user_id: v.string(), run_key: v.string(), trigger: v.string(),
    status: v.optional(v.string()), scheduled_for: v.string(), model_id: v.string(), reason: v.string() })) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (args.p_runs.length > 1000) throw new ConvexError("Fixed-run batch too large");
    let count = 0;
    for (const item of args.p_runs) {
      const existing = await ctx.db.query("analysisJobs")
        .withIndex("by_owner_unique", q => q.eq("owner", item.user_id).eq("uniqueKey", item.run_key)).unique();
      if (!existing) { await put(ctx, "analysisJobs", newRun(item)); count++; }
      else if (existing.status === "cancelled" && time(item.scheduled_for) > Date.now()) {
        await put(ctx, "analysisJobs", { ...parseRow(existing.rowJson), status: "scheduled", completed_at: null,
          error: null, outcome: null, scheduled_for: item.scheduled_for }, existing); count++;
      }
    }
    return count;
  },
});

export const cancelRedundantFollowups = mutation({
  args: { secret: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const pending = await ctx.db.query("analysisJobs").withIndex("by_status_created", q => q.eq("status", "scheduled")).take(1001);
    if (pending.length > 1000) throw new ConvexError("Pending-review scan exceeds transaction limit");
    let count = 0;
    for (const item of pending) {
      const row = parseRow(item.rowJson);
      if (text(row, "trigger") !== "agent_follow_up") continue;
      const fixedReviewAlreadyFirst = pending.some(other => {
        const trigger = text(parseRow(other.rowJson), "trigger");
        return other.owner === item.owner && fixedReviewTriggers.has(trigger) && other.time <= item.time;
      });
      if (fixedReviewAlreadyFirst) {
        await put(ctx, "analysisJobs", { ...row, status: "cancelled", completed_at: new Date().toISOString(), error: "A fixed session review is already scheduled first" }, item); count++;
      }
    }
    return count;
  },
});
