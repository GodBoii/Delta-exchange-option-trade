import { paginationOptsValidator } from "convex/server";
import { v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { orderContext, orderOutcome } from "./orderValidators";
import { authorizeTradingService as authorize } from "./tradingAuth";

// A successful transaction gives exactly one caller permission to dispatch.
// Losing this response requires lookup, not a second dispatch permission.
export const begin = mutation({
  args: {
    secret: v.string(), accountId: v.string(), clientOrderId: v.string(),
    payload: v.string(),
    context: v.optional(orderContext),
  },
  handler: async (ctx, args) => {
    authorize(args.secret);
    if (!args.accountId || !/^[a-zA-Z0-9_-]{1,32}$/.test(args.clientOrderId) || args.payload.length > 16384) {
      throw new Error("Invalid order identity or payload");
    }
    const existing = await ctx.db.query("orderIntents")
      .withIndex("by_account_client", q => q.eq("accountId", args.accountId).eq("clientOrderId", args.clientOrderId))
      .unique();
    if (existing) {
      if (existing.payload !== args.payload) throw new Error("Order identity reused with a different payload");
      if (JSON.stringify(existing.context) !== JSON.stringify(args.context)) throw new Error("Order context changed");
      return { dispatch: false, outcome: existing.outcome };
    }
    await ctx.db.insert("orderIntents", {
      accountId: args.accountId, clientOrderId: args.clientOrderId, payload: args.payload,
      ...(args.context ? { context: args.context } : {}), materialized: false,
      outcome: { kind: "unknown" }, unresolved: true, updatedAt: Date.now(),
    });
    return { dispatch: true, outcome: { kind: "unknown" } };
  },
});

export const forStrategy = query({
  args: { secret: v.string(), accountId: v.string(), strategyId: v.string(), paginationOpts: paginationOptsValidator },
  handler: async (ctx, args) => {
    authorize(args.secret);
    return await ctx.db.query("orderIntents")
      .withIndex("by_account_strategy", q => q.eq("accountId", args.accountId).eq("context.strategyId", args.strategyId))
      .paginate(args.paginationOpts);
  },
});

export const materialized = mutation({
  args: { secret: v.string(), accountId: v.string(), clientOrderId: v.string() },
  handler: async (ctx, args) => {
    authorize(args.secret);
    const intent = await ctx.db.query("orderIntents")
      .withIndex("by_account_client", q => q.eq("accountId", args.accountId).eq("clientOrderId", args.clientOrderId)).unique();
    if (!intent || intent.outcome.kind === "unknown") throw new Error("Order outcome must be known first");
    if (!intent.materialized) await ctx.db.patch(intent._id, { materialized: true });
  },
});

export const resolve = mutation({
  args: {
    secret: v.string(), accountId: v.string(), clientOrderId: v.string(), outcome: orderOutcome,
  },
  handler: async (ctx, args) => {
    authorize(args.secret);
    const existing = await ctx.db.query("orderIntents")
      .withIndex("by_account_client", q => q.eq("accountId", args.accountId).eq("clientOrderId", args.clientOrderId))
      .unique();
    if (!existing) throw new Error("Order intent missing");
    if (args.outcome.kind === "unknown") return;
    if (existing.outcome.kind !== "unknown") {
      if (JSON.stringify(existing.outcome) !== JSON.stringify(args.outcome)) {
        throw new Error("Conflicting order outcome");
      }
      return;
    }
    await ctx.db.patch(existing._id, {
      outcome: args.outcome, unresolved: false, updatedAt: Date.now(),
    });
  },
});

export const unresolved = query({
  args: { secret: v.string(), accountId: v.string(), paginationOpts: paginationOptsValidator },
  handler: async (ctx, args) => {
    authorize(args.secret);
    return await ctx.db.query("orderIntents")
      .withIndex("by_account_unresolved", q => q.eq("accountId", args.accountId).eq("unresolved", true))
      .paginate(args.paginationOpts);
  },
});

export const claimProducts = mutation({
  args: { secret: v.string(), accountId: v.string(), strategyId: v.string(), productIds: v.array(v.string()) },
  handler: async (ctx, args) => {
    authorize(args.secret);
    if (!args.strategyId || !args.accountId || !args.productIds.length || args.productIds.length > 12
        || new Set(args.productIds).size !== args.productIds.length
        || args.productIds.some(id => !/^[1-9][0-9]*$/.test(id))) throw new Error("Invalid product claim");
    for (const productId of args.productIds) {
      const existing = await ctx.db.query("productClaims")
        .withIndex("by_account_product", q => q.eq("accountId", args.accountId).eq("productId", productId)).unique();
      if (existing && existing.strategyId !== args.strategyId) throw new Error("Contract already owned by another strategy");
      if (!existing) await ctx.db.insert("productClaims", {
        accountId: args.accountId, strategyId: args.strategyId, productId,
      });
    }
  },
});

// Only the backend can attest that positions and executable orders are resolved.
export const releaseProducts = mutation({
  args: { secret: v.string(), accountId: v.string(), strategyId: v.string() },
  handler: async (ctx, args) => {
    authorize(args.secret);
    const claims = await ctx.db.query("productClaims")
      .withIndex("by_account_strategy", q => q.eq("accountId", args.accountId).eq("strategyId", args.strategyId)).collect();
    for (const claim of claims) await ctx.db.delete(claim._id);
  },
});
