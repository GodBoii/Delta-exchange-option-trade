import { ConvexError, v } from "convex/values";
import type { MutationCtx, QueryCtx } from "./_generated/server";
import { allocationMode, connectionRecord } from "./applicationValidators";

export const automationPreferences = v.object({
  enabled: v.boolean(), model_id: v.string(),
  minimum_follow_up_minutes: v.number(), maximum_agent_runs_per_day: v.number(),
});
export const userRecord = {
  userId: v.string(),
  capital: v.object({ allocation_mode: allocationMode, capital_amount: v.union(v.string(), v.null()) }),
  automation: automationPreferences, connection: v.union(connectionRecord, v.null()),
  createdAt: v.string(), updatedAt: v.string(),
};
export const defaultAutomation = {
  enabled: false, model_id: "xiaomi/mimo-v2.6-pro",
  minimum_follow_up_minutes: 5, maximum_agent_runs_per_day: 3,
};
export async function findUser(ctx: QueryCtx | MutationCtx, userId: string) {
  return ctx.db.query("users").withIndex("by_user", q => q.eq("userId", userId)).unique();
}
export async function ensureUser(ctx: MutationCtx, userId: string) {
  const existing = await findUser(ctx, userId);
  if (existing) return existing;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(userId)) throw new ConvexError("Invalid Supabase user ID");
  if ((await ctx.db.query("users").take(100)).length >= 100) throw new ConvexError("This deployment supports 100 users");
  const now = new Date().toISOString();
  const id = await ctx.db.insert("users", {
    userId, capital: { allocation_mode: "half_balance", capital_amount: null },
    automation: defaultAutomation, connection: null, createdAt: now, updatedAt: now,
  });
  const created = await ctx.db.get(id);
  if (!created) throw new ConvexError("Account initialization failed");
  return created;
}
export async function systemConfig(ctx: QueryCtx | MutationCtx) {
  return ctx.db.query("systemSettings").withIndex("by_key", q => q.eq("key", "main")).unique();
}
export async function requireOwner(ctx: QueryCtx | MutationCtx, userId: string) {
  if ((await systemConfig(ctx))?.ownerUserId !== userId) throw new ConvexError("Owner access required");
}
