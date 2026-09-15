import { ConvexError, v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { connectionRecord, profileRecord } from "./applicationValidators";
import { authorizeTradingService, authorizeAccountReader } from "./tradingAuth";

export const overview = query({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const profile = await ctx.db.query("profiles").withIndex("by_user", q => q.eq("id", args.userId)).unique();
    const row = await ctx.db.query("exchangeConnections").withIndex("by_user", q => q.eq("user_id", args.userId)).unique();
    const connection = row ? { id: row.id, delta_user_id: row.delta_user_id, account_name: row.account_name,
      email_masked: row.email_masked, environment: row.environment, status: row.status } : null;
    return { profile, connection };
  },
});

export const credentials = query({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    return await ctx.db.query("exchangeConnections").withIndex("by_user", q => q.eq("user_id", args.userId)).unique();
  },
});

export const saveConnection = mutation({
  args: { secret: v.string(), value: connectionRecord, expectedFingerprint: v.union(v.string(), v.null()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const current = await ctx.db.query("exchangeConnections").withIndex("by_user", q => q.eq("user_id", args.value.user_id)).unique();
    if ((current?.fingerprint ?? null) !== args.expectedFingerprint) throw new ConvexError("Connection changed; retry with current credentials");
    if (current) await ctx.db.patch(current._id, args.value);
    else await ctx.db.insert("exchangeConnections", args.value);
    return args.value.id;
  },
});

export const revoke = mutation({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const current = await ctx.db.query("exchangeConnections").withIndex("by_user", q => q.eq("user_id", args.userId)).unique();
    if (current && current.status !== "revoked") await ctx.db.patch(current._id, { status: "revoked", ciphertext: null,
      fingerprint: `revoked:${current.fingerprint}`, updated_at: new Date().toISOString() });
  },
});

export const importProfiles = mutation({
  args: { secret: v.string(), records: v.array(profileRecord) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (process.env.CONVEX_IMPORT_ENABLED !== "true" || args.records.length > 100) throw new ConvexError("Import disabled or batch too large");
    for (const record of args.records) {
      const existing = await ctx.db.query("profiles").withIndex("by_user", q => q.eq("id", record.id)).unique();
      if (existing) {
        if (Object.entries(record).some(([key, value]) => Reflect.get(existing, key) !== value)) throw new ConvexError("Profile import conflicts");
      } else await ctx.db.insert("profiles", record);
    }
  },
});

export const importConnections = mutation({
  args: { secret: v.string(), records: v.array(connectionRecord) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (process.env.CONVEX_IMPORT_ENABLED !== "true" || args.records.length > 100) throw new ConvexError("Import disabled or batch too large");
    for (const record of args.records) {
      const existing = await ctx.db.query("exchangeConnections").withIndex("by_user", q => q.eq("user_id", record.user_id)).unique();
      if (existing) {
        if (existing.id !== record.id || existing.fingerprint !== record.fingerprint || existing.status !== record.status) {
          throw new ConvexError("Connection import conflicts");
        }
      } else await ctx.db.insert("exchangeConnections", record);
    }
  },
});
