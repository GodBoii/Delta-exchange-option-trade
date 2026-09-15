import { paginationOptsValidator } from "convex/server";
import { v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { exchangeFill } from "./fillValidators";
import { authorizeTradingService } from "./tradingAuth";

export const ingest = mutation({
  args: { secret: v.string(), accountId: v.string(), fills: v.array(exchangeFill) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (!args.accountId || args.fills.length > 100) throw new Error("Invalid fill batch");
    for (const fill of args.fills) {
      if (!fill.fillId || !fill.orderId || !/^[1-9][0-9]*$/.test(fill.productId)
          || !/^[1-9][0-9]*$/.test(fill.quantity) || !/^[0-9]+(?:\.[0-9]+)?$/.test(fill.price)
          || (fill.commission !== null && !/^-?[0-9]+(?:\.[0-9]+)?$/.test(fill.commission))
          || !Number.isFinite(Date.parse(fill.occurredAt))) throw new Error("Invalid fill");
      const existing = await ctx.db.query("exchangeFills")
        .withIndex("by_account_fill", q => q.eq("accountId", args.accountId).eq("fillId", fill.fillId)).unique();
      if (!existing) {
        await ctx.db.insert("exchangeFills", { accountId: args.accountId, ...fill });
      } else {
        if (existing.productId !== fill.productId || existing.orderId !== fill.orderId
            || existing.side !== fill.side || existing.quantity !== fill.quantity
            || existing.price !== fill.price || existing.occurredAt !== fill.occurredAt) {
          throw new Error("Conflicting fill facts");
        }
        // A fee can arrive later. Older incomplete observations cannot erase it.
        if (fill.commission !== null && existing.commission !== fill.commission) {
          if (existing.commission !== null) throw new Error("Conflicting final commission");
          await ctx.db.patch(existing._id, { commission: fill.commission });
        }
      }
    }
  },
});

export const forProduct = query({
  args: {
    secret: v.string(), accountId: v.string(), productId: v.string(),
    from: v.string(), paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    return await ctx.db.query("exchangeFills")
      .withIndex("by_account_product_time", q => q.eq("accountId", args.accountId)
        .eq("productId", args.productId).gte("occurredAt", args.from))
      .paginate(args.paginationOpts);
  },
});
