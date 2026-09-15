import { ConvexError, v } from "convex/values";
import { mutation } from "./_generated/server";
import { get, ownerRows, parseRow, put, text, time, type Row } from "./runtimeRecords";
import { authorizeTradingService } from "./tradingAuth";

export function uuid() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, char => {
    const random = Math.floor(Math.random() * 16);
    return (char === "x" ? random : (random & 3) | 8).toString(16);
  });
}
export function newRun(value: Row): Row {
  const now = new Date().toISOString();
  return { id: uuid(), created_at: now, updated_at: now, status: "scheduled", outcome: null, parent_agent_run_id: null,
    model_id: "deepseek/deepseek-v4.1-flash", signals_to_inspect: [], ...value };
}

export const reserveCapital = mutation({
  args: { secret: v.string(), p_user_id: v.string(), p_strategy_id: v.string(), p_maximum_slots: v.number() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (!Number.isInteger(args.p_maximum_slots) || args.p_maximum_slots < 1 || args.p_maximum_slots > 100) throw new ConvexError("Invalid slot count");
    const strategy = await get(ctx, "strategies", args.p_strategy_id);
    if (!strategy || strategy.owner !== args.p_user_id) throw new ConvexError("Strategy unavailable");
    const slots = await ownerRows(ctx, "strategy_capital_slots", args.p_user_id);
    const active = slots.filter(slot => ["reserved", "active"].includes(slot.status));
    const existing = active.find(slot => slot.relation === args.p_strategy_id);
    if (existing) return { slot: parseRow(existing.rowJson).slot_number, created: false, occupiedBefore: active.length - 1 };
    if (active.length >= args.p_maximum_slots) throw new ConvexError({ code: "capital_slots_full", message: "All capital allocations are occupied" });
    for (let slot = 1; slot <= args.p_maximum_slots; slot++) {
      const current = slots.find(row => parseRow(row.rowJson).slot_number === slot);
      if (current && current.status !== "available") continue;
      await put(ctx, "strategy_capital_slots", {
        id: current?.externalId ?? uuid(), user_id: args.p_user_id, slot_number: slot, strategy_id: args.p_strategy_id,
        proposal_id: null, status: "reserved", reserved_at: new Date().toISOString(), released_at: null,
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
    const running = await ctx.db.query("automation_agent_runs")
      .withIndex("by_owner_status_created", q => q.eq("owner", args.p_user_id).eq("status", "running")).first();
    if (running) return [];
    const run = await get(ctx, "automation_agent_runs", args.p_run_id);
    if (!run || run.owner !== args.p_user_id || run.status !== "scheduled") return [];
    const row = { ...parseRow(run.rowJson), status: "running", started_at: new Date().toISOString(), error: null };
    await put(ctx, "automation_agent_runs", row, run);
    return [row];
  },
});

export const ensureFixedRuns = mutation({
  args: { secret: v.string(), p_runs: v.array(v.object({ user_id: v.string(), run_key: v.string(), trigger: v.string(),
    scheduled_for: v.string(), model_id: v.string(), reason: v.string() })) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (args.p_runs.length > 1000) throw new ConvexError("Fixed-run batch too large");
    let count = 0;
    for (const item of args.p_runs) {
      const existing = await ctx.db.query("automation_agent_runs")
        .withIndex("by_owner_unique", q => q.eq("owner", item.user_id).eq("uniqueKey", item.run_key)).unique();
      if (!existing) { await put(ctx, "automation_agent_runs", newRun(item)); count++; }
      else if (existing.status === "cancelled" && time(item.scheduled_for) > Date.now()) {
        await put(ctx, "automation_agent_runs", { ...parseRow(existing.rowJson), status: "scheduled", completed_at: null,
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
    const pending = await ctx.db.query("automation_agent_runs").withIndex("by_status_created", q => q.eq("status", "scheduled")).take(1001);
    if (pending.length > 1000) throw new ConvexError("Pending-review scan exceeds transaction limit");
    let count = 0;
    for (const item of pending) {
      const row = parseRow(item.rowJson);
      if (text(row, "trigger") !== "agent_follow_up") continue;
      if (pending.some(other => other.owner === item.owner && ["asia_session", "london_session", "new_york_session", "pre_expiry"].includes(text(parseRow(other.rowJson), "trigger")) && other.time <= item.time)) {
        await put(ctx, "automation_agent_runs", { ...row, status: "cancelled", completed_at: new Date().toISOString(), error: "A fixed session review is already scheduled first" }, item); count++;
      }
    }
    return count;
  },
});
