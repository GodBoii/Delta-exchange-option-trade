import { ConvexError, v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { connectionRecord } from "./applicationValidators";
import { authorizeTradingService, authorizeAccountReader } from "./tradingAuth";
import { ensureUser, findUser, systemConfig, userRecord, defaultAutomation } from "./userRecords";

export const overview = query({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const row = (await findUser(ctx, args.userId))?.connection;
    return { connection: row ? {
      id: row.id, delta_user_id: row.delta_user_id, account_name: row.account_name,
      email_masked: row.email_masked, environment: row.environment, status: row.status,
    } : null };
  },
});
export const credentials = query({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    return (await findUser(ctx, args.userId))?.connection ?? null;
  },
});
export const saveConnection = mutation({
  args: { secret: v.string(), value: connectionRecord, expectedFingerprint: v.union(v.string(), v.null()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const user = await ensureUser(ctx, args.value.user_id);
    if ((user.connection?.fingerprint ?? null) !== args.expectedFingerprint) throw new ConvexError("Connection changed; retry with current credentials");
    await ctx.db.patch(user._id, { connection: args.value, updatedAt: new Date().toISOString() });
    return args.value.id;
  },
});
export const revoke = mutation({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const user = await findUser(ctx, args.userId);
    if (user?.connection && user.connection.status !== "revoked") await ctx.db.patch(user._id, {
      connection: { ...user.connection, status: "revoked", ciphertext: null,
        fingerprint: "revoked:" + user.connection.fingerprint, updated_at: new Date().toISOString() },
      updatedAt: new Date().toISOString(),
    });
  },
});
export const provision = mutation({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => { authorizeTradingService(args.secret); await ensureUser(ctx, args.userId); },
});
export const importUsers = mutation({
  args: { secret: v.string(), records: v.array(v.object(userRecord)), ownerUserId: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (process.env.CONVEX_IMPORT_ENABLED !== "true" || args.records.length > 100) throw new ConvexError("Import disabled");
    for (const record of args.records) {
      const current = await findUser(ctx, record.userId);
      if (current) {
        for (const [key, value] of Object.entries(record)) {
          if (JSON.stringify(Reflect.get(current, key)) !== JSON.stringify(value)) throw new ConvexError("User import conflicts");
        }
      } else {
        const target = await ensureUser(ctx, record.userId);
        await ctx.db.replace(target._id, record);
      }
    }
    const config = await systemConfig(ctx);
    if (config && config.ownerUserId !== args.ownerUserId) throw new ConvexError("Owner import conflicts");
    if (!config) await ctx.db.insert("systemSettings", {
      key: "main", ownerUserId: args.ownerUserId, outboundIp: null, ipCheckedAt: null,
      analysis: { ...defaultAutomation, enabled: true },
    });
  },
});
export const serverUsers = query({
  args: { secret: v.string() },
  handler: async (ctx, args) => { authorizeTradingService(args.secret); return ctx.db.query("users").take(101); },
});
export const executionGroups = query({
  args: { secret: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    return (await ctx.db.query("users").take(101)).map(user => ({
      userId: user.userId, accountId: user.connection?.delta_user_id ?? user.userId,
    }));
  },
});
export const updateOutboundIp = mutation({
  args: { secret: v.string(), ip: v.string() },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (!/^[0-9a-fA-F.:]{3,45}$/.test(args.ip)) throw new ConvexError("Invalid IP address");
    const config = await systemConfig(ctx);
    if (!config) throw new ConvexError("System configuration missing");
    await ctx.db.patch(config._id, { outboundIp: args.ip, ipCheckedAt: new Date().toISOString() });
  },
});
