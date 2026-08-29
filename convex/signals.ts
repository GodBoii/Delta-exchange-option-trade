import { v } from "convex/values";
import { mutation, query } from "./_generated/server";

const scope = v.union(v.literal("automation"), v.literal("strategies"));

export const latest = query({
  args: { scope },
  handler: async (ctx, args) => {
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) return null;
    return await ctx.db
      .query("signals")
      .withIndex("by_user_scope_updated", q => q.eq("userId", identity.subject).eq("scope", args.scope))
      .order("desc")
      .first();
  },
});

export const publish = mutation({
  args: {
    syncSecret: v.string(),
    userId: v.string(),
    scope,
    entityId: v.string(),
    status: v.string(),
    outcome: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    if (!process.env.CONVEX_SYNC_SECRET || args.syncSecret !== process.env.CONVEX_SYNC_SECRET) {
      throw new Error("Unauthorized signal publisher");
    }
    const existing = await ctx.db
      .query("signals")
      .withIndex("by_scope_entity", q => q.eq("scope", args.scope).eq("entityId", args.entityId))
      .unique();
    const value = {
      userId: args.userId,
      scope: args.scope,
      entityId: args.entityId,
      status: args.status,
      ...(args.outcome ? { outcome: args.outcome } : {}),
      updatedAt: Date.now(),
    };
    if (existing) {
      await ctx.db.patch(existing._id, value);
      return existing._id;
    }
    return await ctx.db.insert("signals", value);
  },
});
