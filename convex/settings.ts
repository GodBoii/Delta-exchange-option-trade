import { ConvexError, v } from "convex/values";
import { mutation, query, type MutationCtx, type QueryCtx } from "./_generated/server";
import { authorizeTradingService } from "./tradingAuth";
import { automationPreferences, defaultAutomation, ensureUser, findUser, systemConfig } from "./userRecords";

export const globalScope = "global";
export async function getAutomation(ctx: QueryCtx | MutationCtx, userId: string) {
  const value = userId === globalScope ? (await systemConfig(ctx))?.analysis : (await findUser(ctx, userId))?.automation;
  return value ? { user_id: userId, ...value } : null;
}
export const listAutomation = query({
  args: { secret: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const users = await ctx.db.query("users").take(101);
    if (users.length > 100) throw new ConvexError("Account limit exceeded");
    const global = await getAutomation(ctx, globalScope);
    return [...users.map(user => ({ user_id: user.userId, ...user.automation })), ...(global ? [global] : [])];
  },
});
export const saveAutomation = mutation({
  args: { secret: v.string(), userId: v.string(), value: automationPreferences },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (args.value.minimum_follow_up_minutes < 5 || args.value.maximum_agent_runs_per_day < 0) throw new ConvexError("Invalid analysis limits");
    if (args.userId === globalScope) {
      const config = await systemConfig(ctx);
      if (!config) throw new ConvexError("Global configuration missing");
      await ctx.db.patch(config._id, { analysis: args.value });
    } else {
      const user = await ensureUser(ctx, args.userId);
      await ctx.db.patch(user._id, { automation: args.value, updatedAt: new Date().toISOString() });
    }
    return { user_id: args.userId, ...args.value };
  },
});
export const defaults = defaultAutomation;
