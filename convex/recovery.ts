import { v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { authorizeTradingService } from "./tradingAuth";

const recoveryItem = v.object({
  entityType: v.string(), entityKey: v.string(), revision: v.number(),
  operation: v.union(v.literal("upsert"), v.literal("delete")), payloadJson: v.optional(v.string()),
});

function summary(entityType: string, payloadJson: string | undefined) {
  if (!payloadJson) return null;
  const envelope: unknown = JSON.parse(payloadJson);
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) return null;
  const source = envelope as Record<string, unknown>;
  const data = entityType === "strategies" || entityType === "analysis_jobs" || entityType === "strategy_proposals"
    ? source.data : source;
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const row = data as Record<string, unknown>;
  if (entityType === "saved_strategies") return {
    id: row.id, user_id: row.user_id, name: row.name, definition_json: row.definition_json,
    source_run_id: row.source_run_id, enabled_for_ai: row.enabled_for_ai,
    version: row.version, deleted: row.deleted,
    created_at: row.created_at, updated_at: row.updated_at,
  };
  if (entityType === "strategies") return {
    id: row.id, user_id: row.user_id, name: row.name, status: row.status,
    entry_at: row.entry_at, exit_at: row.exit_at, entry_execution_at: row.entry_execution_at,
    exit_execution_at: row.exit_execution_at, created_at: row.created_at,
  };
  if (entityType === "analysis_jobs") return {
    id: row.id, user_id: row.user_id, trigger: row.trigger, status: row.status,
    outcome: row.outcome, scheduled_for: row.scheduled_for, started_at: row.started_at,
    completed_at: row.completed_at,
  };
  return null;
}

function owner(entityType: string, payloadJson: string | undefined): string | null {
  if (!payloadJson) return null;
  const envelope: unknown = JSON.parse(payloadJson);
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) return null;
  const source = envelope as Record<string, unknown>;
  const row = source.data && typeof source.data === "object" && !Array.isArray(source.data)
    ? source.data as Record<string, unknown> : source;
  const id = entityType === "saved_strategies" ? row.user_id : row.user_id;
  return typeof id === "string" ? id : entityType === "saved_strategies" && id === null ? "global" : null;
}

function closedAt(entityType: string, payloadJson: string | undefined, now: number): number | undefined {
  if (!payloadJson) return now;
  const envelope: unknown = JSON.parse(payloadJson);
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) return undefined;
  const source = envelope as Record<string, unknown>;
  const row = source.data && typeof source.data === "object" && !Array.isArray(source.data)
    ? source.data as Record<string, unknown> : source;
  const status = row.status;
  if (["exchange_fills", "order_intents", "executions", "execution_orders"].includes(entityType)) {
    const timestamp = Date.parse(String(row.recovery_closed_at ?? ""));
    return Number.isFinite(timestamp) ? timestamp : undefined;
  }
  if (entityType === "strategies" && ["completed", "cancelled"].includes(String(status))) return now;
  if (entityType === "analysis_jobs" && ["completed", "failed", "cancelled"].includes(String(status))) return now;
  if (entityType === "strategy_proposals" && ["rejected", "expired", "activated", "cancelled"].includes(String(status))) return now;
  return undefined;
}

export const applyBatch = mutation({
  args: { secret: v.string(), items: v.array(recoveryItem) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (args.items.length > 25) throw new Error("Recovery batch exceeds 25 items");
    const now = Date.now();
    for (const item of args.items) {
      if (!Number.isSafeInteger(item.revision) || item.revision < 1 ||
          item.entityType.length > 80 || item.entityKey.length > 200 ||
          (item.payloadJson?.length ?? 0) > 262144) throw new Error("Invalid recovery item");
      const existing = await ctx.db.query("recoveryRecords")
        .withIndex("by_entity", q => q.eq("entityType", item.entityType).eq("entityKey", item.entityKey)).unique();
      if (existing && existing.revision >= item.revision) continue;
      const payloadJson = item.operation === "delete" ? null : item.payloadJson ?? null;
      const value = { entityType: item.entityType, entityKey: item.entityKey,
        revision: item.revision, payloadJson, updatedAt: now,
        closedAt: closedAt(item.entityType, payloadJson ?? undefined, now) };
      if (existing) await ctx.db.replace(existing._id, value);
      else await ctx.db.insert("recoveryRecords", value);

      const view = await ctx.db.query("recoveryViews")
        .withIndex("by_entity", q => q.eq("entityType", item.entityType).eq("entityKey", item.entityKey)).unique();
      const safe = summary(item.entityType, item.payloadJson);
      const userId = owner(item.entityType, item.payloadJson);
      if (payloadJson && safe && userId) {
        const next = { userId, entityType: item.entityType, entityKey: item.entityKey,
          revision: item.revision, summary: safe, updatedAt: now };
        if (view) await ctx.db.replace(view._id, next); else await ctx.db.insert("recoveryViews", next);
      } else if (view) await ctx.db.delete(view._id);
    }
    return args.items.length;
  },
});

export const saveManifest = mutation({
  args: { secret: v.string(), day: v.string(), lastOutboxId: v.number(), countsJson: v.string(), checksum: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(args.day) || args.countsJson.length > 8192) throw new Error("Invalid manifest");
    const value = { day: args.day, lastOutboxId: args.lastOutboxId,
      countsJson: args.countsJson, checksum: args.checksum, updatedAt: Date.now() };
    const existing = await ctx.db.query("recoveryManifests").withIndex("by_day", q => q.eq("day", args.day)).unique();
    if (existing) await ctx.db.replace(existing._id, value); else await ctx.db.insert("recoveryManifests", value);
  },
});

export const prunePage = mutation({
  args: { secret: v.string(), before: v.number() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (!Number.isFinite(args.before) || args.before < 0) throw new Error("Invalid recovery cutoff");
    const records = await ctx.db.query("recoveryRecords")
      .withIndex("by_closed", q => q.gte("closedAt", 0).lt("closedAt", args.before)).take(25);
    for (const record of records) {
      const view = await ctx.db.query("recoveryViews")
        .withIndex("by_entity", q => q.eq("entityType", record.entityType).eq("entityKey", record.entityKey)).unique();
      if (view) await ctx.db.delete(view._id);
      await ctx.db.delete(record._id);
    }
    const day = new Date(args.before).toISOString().slice(0, 10);
    const manifests = await ctx.db.query("recoveryManifests")
      .withIndex("by_day", q => q.lt("day", day)).take(25);
    for (const manifest of manifests) await ctx.db.delete(manifest._id);
    return records.length + manifests.length;
  },
});

export const readOnlyForUser = query({
  args: { entityType: v.union(v.literal("strategies"), v.literal("analysis_jobs"), v.literal("saved_strategies")) },
  handler: async (ctx, args) => {
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) return [];
    const own = await ctx.db.query("recoveryViews")
      .withIndex("by_user_type", q => q.eq("userId", identity.subject).eq("entityType", args.entityType))
      .order("desc").take(100);
    if (args.entityType !== "saved_strategies" && args.entityType !== "analysis_jobs") return own;
    const shared = await ctx.db.query("recoveryViews")
      .withIndex("by_user_type", q => q.eq("userId", "global").eq("entityType", args.entityType))
      .order("desc").take(100);
    return [...own, ...shared];
  },
});
