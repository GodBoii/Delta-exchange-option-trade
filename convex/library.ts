import { paginationOptsValidator } from "convex/server";
import { ConvexError, v } from "convex/values";
import { mutation, query } from "./_generated/server";
import { savedStrategyRecord, capitalRecord } from "./applicationValidators";
import { authorizeTradingService, authorizeAccountReader } from "./tradingAuth";
import { ensureUser, findUser, systemConfig } from "./userRecords";
import { cleanStrategyName } from "../lib/strategy-name";

function validateDefinition(name: string, definitionJson: string, enabled: boolean) {
  if (name !== cleanStrategyName(name)) throw new ConvexError("Keep version numbers out of strategy names");
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

function requireLibraryWrites() {
  if (process.env.CONVEX_LIBRARY_WRITES_PAUSED === "true") {
    throw new ConvexError("Strategy library is paused for the local storage transfer");
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
    requireLibraryWrites();
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) throw new ConvexError("Sign in to save a strategy");
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(args.id)) throw new ConvexError("Invalid strategy ID");
    validateDefinition(args.name, args.definitionJson, args.enabled);
    const owner = (await systemConfig(ctx))?.ownerUserId === identity.subject;
    const existing = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", args.id)).unique();
    const now = new Date().toISOString();
    if (existing) {
      if ((existing.user_id !== identity.subject && !(owner && existing.user_id === null)) || existing.deleted) throw new ConvexError("Strategy unavailable");
      if (existing.name === args.name && existing.definitionJson === args.definitionJson && existing.enabled_for_ai === args.enabled) return existing;
      if (args.expectedVersion !== existing.version) throw new ConvexError("Strategy changed in another session. Reload before saving.");
      const update = { name: args.name, definitionJson: args.definitionJson, enabled_for_ai: args.enabled,
        version: existing.version + 1, updated_at: now };
      await ctx.db.patch(existing._id, update);
      return { ...existing, ...update };
    }
    if (args.expectedVersion !== null) throw new ConvexError("Strategy no longer exists");
    const value = { id: args.id, user_id: owner ? null : identity.subject, name: args.name, definitionJson: args.definitionJson,
      enabled_for_ai: args.enabled, version: 1, source_run_id: null, created_at: now, updated_at: now, deleted: false };
    await ctx.db.insert("savedStrategies", value);
    return value;
  },
});

export const remove = mutation({
  args: { id: v.string(), expectedVersion: v.number() },
  handler: async (ctx, args) => {
    requireLibraryWrites();
    const identity = await ctx.auth.getUserIdentity();
    if (!identity) throw new ConvexError("Sign in to delete a strategy");
    const existing = await ctx.db.query("savedStrategies").withIndex("by_external_id", q => q.eq("id", args.id)).unique();
    const owner = (await systemConfig(ctx))?.ownerUserId === identity.subject;
    if (!existing || (existing.user_id !== identity.subject && !(owner && existing.user_id === null))) throw new ConvexError("Strategy unavailable");
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
    requireLibraryWrites();
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

/** Add a named shared template without changing existing versions or run snapshots. */
export const serverCreateDefault = mutation({
  args: { secret: v.string(), id: v.string(), definitionJson: v.string() },
  handler: async (ctx, args) => {
    requireLibraryWrites();
    authorizeTradingService(args.secret);
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(args.id)) {
      throw new ConvexError("Invalid strategy ID");
    }
    const definition: unknown = JSON.parse(args.definitionJson);
    if (!definition || typeof definition !== "object" || !("name" in definition)
        || typeof definition.name !== "string") throw new ConvexError("Invalid strategy definition");
    const name = definition.name;
    validateDefinition(name, args.definitionJson, true);
    const existing = await ctx.db.query("savedStrategies")
      .withIndex("by_external_id", q => q.eq("id", args.id)).unique();
    if (existing) {
      if (existing.user_id !== null || existing.name !== name || existing.deleted
          || existing.definitionJson !== args.definitionJson) {
        throw new ConvexError("Shared strategy ID is already owned");
      }
      return existing;
    }
    const sameName = await ctx.db.query("savedStrategies")
      .withIndex("by_owner_deleted", q => q.eq("user_id", null).eq("deleted", false)).collect();
    if (sameName.some(item => item.name.toLowerCase() === name.toLowerCase())) {
      throw new ConvexError("Shared strategy name already exists");
    }
    const now = new Date().toISOString();
    const value = { id: args.id, user_id: null, name, definitionJson: args.definitionJson,
      source_run_id: null, version: 1, enabled_for_ai: true, created_at: now, updated_at: now, deleted: false };
    await ctx.db.insert("savedStrategies", value);
    return value;
  },
});

export const serverRetireDefault = mutation({
  args: {
    secret: v.string(),
    id: v.string(),
    expectedVersion: v.number(),
  },
  handler: async (ctx, args) => {
    requireLibraryWrites();
    authorizeTradingService(args.secret);
    const existing = await ctx.db.query("savedStrategies")
      .withIndex("by_external_id", q => q.eq("id", args.id))
      .unique();
    if (!existing || existing.user_id !== null) throw new ConvexError("Default strategy unavailable");
    if (existing.deleted) return existing;
    if (existing.version !== args.expectedVersion) {
      throw new ConvexError("Default strategy changed. Reload before retiring.");
    }
    const update = {
      deleted: true,
      enabled_for_ai: false,
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
    return { user_id: args.userId, ...((await findUser(ctx, args.userId))?.capital ?? { allocation_mode: "half_balance", capital_amount: null }) };
  },
});

export const setCapital = mutation({
  args: { secret: v.string(), value: capitalRecord },
  handler: async (ctx, args) => {
    requireLibraryWrites();
    authorizeTradingService(args.secret);
    const amount = args.value.capital_amount;
    if (args.value.allocation_mode === "fixed_amount") {
      if (amount === null || !/^[0-9]+(?:\.[0-9]+)?$/.test(amount) || !(Number(amount) > 0) || !Number.isFinite(Number(amount))) throw new ConvexError("Invalid capital amount");
    } else if (amount !== null) throw new ConvexError("Percentage allocation cannot have a fixed amount");
    const user = await ensureUser(ctx, args.value.user_id);
    await ctx.db.patch(user._id, { capital: { allocation_mode: args.value.allocation_mode, capital_amount: amount }, updatedAt: new Date().toISOString() });
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
