import { ConvexError, v } from "convex/values";
import { mutation } from "./_generated/server";
import { savedStrategyRecord } from "./applicationValidators";
import { authorizeTradingService } from "./tradingAuth";
import { findUser } from "./userRecords";

// The inactive Convex catalog predates the live SQL catalog. Replacement is
// allowed only during the stopped-writer migration and checks the old content.
export const replaceCatalog = mutation({
  args: { secret: v.string(), record: savedStrategyRecord,
    expectedVersion: v.union(v.number(), v.null()), expectedDefinition: v.union(v.string(), v.null()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (process.env.CONVEX_IMPORT_ENABLED !== "true") throw new ConvexError("Import disabled");
    const current = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", args.record.id)).unique();
    if ((current?.version ?? null) !== args.expectedVersion || (current?.definitionJson ?? null) !== args.expectedDefinition) {
      throw new ConvexError("Catalog changed during migration");
    }
    if (current) await ctx.db.replace(current._id, { ...args.record, deleted: false });
    else await ctx.db.insert("savedStrategies", { ...args.record, deleted: false });
  },
});

export const removeDeletedUserSignals = mutation({
  args: { secret: v.string(), userIds: v.array(v.string()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (process.env.CONVEX_IMPORT_ENABLED !== "true" || args.userIds.length > 100) throw new ConvexError("Import disabled");
    let removed = 0;
    for (const userId of args.userIds) {
      if (userId === "global" || await findUser(ctx, userId)) throw new ConvexError("Cannot remove a retained user's signals");
      for (const scope of ["automation", "strategies"] as const) {
        const records = await ctx.db.query("signals").withIndex("by_user_scope_updated",
          q => q.eq("userId", userId).eq("scope", scope)).take(1000);
        for (const record of records) { await ctx.db.delete(record._id); removed++; }
      }
    }
    return removed;
  },
});
