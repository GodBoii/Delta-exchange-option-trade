import { paginationOptsValidator } from "convex/server";
import { ConvexError, v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { savedStrategyRecord, capitalRecord } from "./applicationValidators";
import { authorizeTradingService, authorizeAccountReader } from "./tradingAuth";

function validateDefinition(name: string, definitionJson: string, enabled: boolean) {
  if (name.trim().length < 2 || name.length > 80 || definitionJson.length > 262144) {
    throw new ConvexError("Invalid strategy name or definition size");
  }
  const definition: unknown = JSON.parse(definitionJson);
  if (definition === null || typeof definition !== "object"
      || !("name" in definition) || definition.name !== name
      || !("legs" in definition) || !Array.isArray(definition.legs)
      || definition.legs.length < 1 || definition.legs.length > 12
      || !("enabledForAi" in definition) || definition.enabledForAi !== enabled) {
    throw new ConvexError("Strategy definition does not match its metadata");
  }
}

export const list = query({
  args: { defaults: v.boolean(), paginationOpts: paginationOptsValidator },
  handler: async (ctx, args) => {
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) throw new ConvexError("Sign in to read the strategy library");
    return await ctx.db.query("savedStrategies")
      .withIndex("by_owner_deleted", q => q.eq("user_id", args.defaults ? null : identity.subject).eq("deleted", false))
      .paginate(args.paginationOpts);
  },
});

export const save = mutation({
  args: { id: v.string(), name: v.string(), definitionJson: v.string(), enabled: v.boolean(), expectedVersion: v.union(v.number(), v.null()) },
  handler: async (ctx, args) => {
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) throw new ConvexError("Sign in to save a strategy");
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(args.id)) throw new ConvexError("Invalid strategy ID");
    validateDefinition(args.name, args.definitionJson, args.enabled);
    const existing = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", args.id)).unique();
    const now = new Date().toISOString();
    if (existing) {
      if (existing.user_id !== identity.subject || existing.deleted) throw new ConvexError("Strategy unavailable");
      if (existing.name === args.name && existing.definitionJson === args.definitionJson && existing.enabled_for_ai === args.enabled) return existing;
      if (args.expectedVersion !== existing.version) throw new ConvexError("Strategy changed in another session. Reload before saving.");
      const update = { name: args.name, definitionJson: args.definitionJson, enabled_for_ai: args.enabled,
        version: existing.version + 1, updated_at: now };
      await ctx.db.patch(existing._id, update);
      return { ...existing, ...update };
    }
    if (args.expectedVersion !== null) throw new ConvexError("Strategy no longer exists");
    const value = { id: args.id, user_id: identity.subject, name: args.name, definitionJson: args.definitionJson,
      enabled_for_ai: args.enabled, version: 1, source_run_id: null, created_at: now, updated_at: now, deleted: false };
    await ctx.db.insert("savedStrategies", value);
    return value;
  },
});

export const remove = mutation({
  args: { id: v.string(), expectedVersion: v.number() },
  handler: async (ctx, args) => {
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) throw new ConvexError("Sign in to delete a strategy");
    const existing = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", args.id)).unique();
    if (!existing || existing.user_id !== identity.subject) throw new ConvexError("Strategy unavailable");
    if (existing.deleted) return;
    if (existing.version !== args.expectedVersion) throw new ConvexError("Strategy changed. Reload before deleting.");
    await ctx.db.patch(existing._id, { deleted: true, enabled_for_ai: false, updated_at: new Date().toISOString() });
  },
});

export const serverList = query({
  args: { secret: v.string(), userId: v.string(), defaults: v.boolean(), paginationOpts: paginationOptsValidator },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    return await ctx.db.query("savedStrategies")
      .withIndex("by_owner_deleted", q => q.eq("user_id", args.defaults ? null : args.userId).eq("deleted", false))
      .paginate(args.paginationOpts);
  },
});

export const serverGet = query({
  args: { secret: v.string(), userId: v.string(), id: v.string() },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    const record = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", args.id)).unique();
    return record && !record.deleted && (record.user_id === null || record.user_id === args.userId) ? record : null;
  },
});

export const serverUpdateDefault = mutation({
  args: {
    secret: v.string(),
    id: v.string(),
    definitionJson: v.string(),
    expectedVersion: v.number(),
  },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const existing = await ctx.db.query("savedStrategies")
      .withIndex("by_external_id", q => q.eq("id", args.id))
      .unique();
    if (!existing || existing.deleted || existing.user_id !== null) {
      throw new ConvexError("Default strategy unavailable");
    }
    validateDefinition(existing.name, args.definitionJson, existing.enabled_for_ai);
    if (existing.definitionJson === args.definitionJson) return existing;
    if (existing.version !== args.expectedVersion) {
      throw new ConvexError("Default strategy changed. Reload before updating.");
    }
    const update = {
      definitionJson: args.definitionJson,
      version: existing.version + 1,
      updated_at: new Date().toISOString(),
    };
    await ctx.db.patch(existing._id, update);
    return { ...existing, ...update };
  },
});

export const getCapital = query({
  args: { secret: v.string(), userId: v.string() },
  handler: async (ctx, args) => {
    authorizeAccountReader(args.secret);
    return await ctx.db.query("capitalSettings").withIndex("by_user", q => q.eq("user_id", args.userId)).unique()
      ?? { user_id: args.userId, allocation_mode: "half_balance", capital_amount: null };
  },
});

export const setCapital = mutation({
  args: { secret: v.string(), value: capitalRecord },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const amount = args.value.capital_amount;
    if (args.value.allocation_mode === "fixed_amount") {
      if (amount === null || !/^[0-9]+(?:\.[0-9]+)?$/.test(amount) || !(Number(amount) > 0) || !Number.isFinite(Number(amount))) throw new ConvexError("Invalid capital amount");
    } else if (amount !== null) throw new ConvexError("Percentage allocation cannot have a fixed amount");
    const existing = await ctx.db.query("capitalSettings").withIndex("by_user", q => q.eq("user_id", args.value.user_id)).unique();
    if (existing) await ctx.db.patch(existing._id, args.value);
    else await ctx.db.insert("capitalSettings", args.value);
    return args.value;
  },
});

// Imports are explicitly enabled during the paused cutover and never overwrite live edits.
export const importStrategies = mutation({
  args: { secret: v.string(), records: v.array(savedStrategyRecord) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (process.env.CONVEX_IMPORT_ENABLED !== "true" || args.records.length > 100) throw new ConvexError("Import disabled or batch too large");
    for (const record of args.records) {
      if (!Number.isInteger(record.version) || record.version < 1) throw new ConvexError("Invalid source version");
      const existing = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", record.id)).unique();
      if (existing) {
        if (Object.entries(record).some(([key, value]) => Reflect.get(existing, key) !== value) || existing.deleted) {
          throw new ConvexError("Import conflicts with an existing strategy");
        }
      } else await ctx.db.insert("savedStrategies", { ...record, deleted: false });
    }
  },
});

export const importCapital = mutation({
  args: { secret: v.string(), records: v.array(capitalRecord) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (process.env.CONVEX_IMPORT_ENABLED !== "true" || args.records.length > 100) throw new ConvexError("Import disabled or batch too large");
    for (const record of args.records) {
      const existing = await ctx.db.query("capitalSettings").withIndex("by_user", q => q.eq("user_id", record.user_id)).unique();
      if (existing) {
        if (existing.allocation_mode !== record.allocation_mode || existing.capital_amount !== record.capital_amount) {
          throw new ConvexError("Import conflicts with an existing capital policy");
        }
      } else await ctx.db.insert("capitalSettings", record);
    }
  },
});
