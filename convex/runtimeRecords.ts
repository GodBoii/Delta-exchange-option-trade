import { paginationOptsValidator } from "convex/server";
import { ConvexError, v, type Infer } from "convex/values";
import { mutation, query, type MutationCtx, type QueryCtx } from "./_generated/server";
import { runtimeTableName } from "./runtimeTables";
import { authorizeTradingService } from "./tradingAuth";
import type { Doc } from "./_generated/dataModel";

export type RuntimeTable = Infer<typeof runtimeTableName>;
export type Row = Record<string, unknown>;
export function parseRow(text: string): Row {
  const value: unknown = JSON.parse(text);
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new ConvexError("Expected a record");
  return Object.fromEntries(Object.entries(value));
}
export function text(row: Row, key: string): string { return typeof row[key] === "string" ? row[key] : ""; }
export function time(value: unknown): number {
  if (value === null || value === undefined) return 0;
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) throw new ConvexError("Invalid record timestamp");
  return Date.parse(value);
}
export async function get(ctx: QueryCtx | MutationCtx, table: RuntimeTable, id: string) {
  return await ctx.db.query(table).withIndex("by_external", q => q.eq("externalId", id)).unique();
}
export async function ownerRows(ctx: QueryCtx | MutationCtx, table: RuntimeTable, owner: string) {
  // Control-plane transactions only. Larger accounts must paginate through select.
  const rows = await ctx.db.query(table).withIndex("by_owner_created", q => q.eq("owner", owner)).take(1001);
  if (rows.length > 1000) throw new ConvexError("Control transaction exceeds its bounded record window");
  return rows;
}

export async function put(ctx: MutationCtx, table: RuntimeTable, row: Row, existing?: Awaited<ReturnType<typeof get>>) {
  const externalId = text(row, "id") || text(row, "user_id");
  if (!externalId) throw new ConvexError("Record identity missing");
  let owner = text(row, "user_id");
  const relation = text(row, "execution_id") || text(row, "strategy_id") || text(row, "strategy_proposal_id");
  if (table === "executions" || table === "execution_orders") {
    const parent = await get(ctx, table === "executions" ? "strategies" : "executions", relation);
    if (!parent) throw new ConvexError("Execution parent missing");
    owner = parent.owner;
  }
  if (!owner || (existing && existing.owner !== owner)) throw new ConvexError("Record owner cannot change");
  if (existing && ["executions", "execution_orders"].includes(table) && existing.relation !== relation) throw new ConvexError("Execution parent cannot change");
  const uniqueKey = table === "execution_orders" ? text(row, "client_order_id")
    : table === "automation_agent_runs" ? text(row, "run_key")
    : table === "strategy_capital_slots" ? String(row.slot_number)
    : table === "automation_settings" ? owner : externalId;
  if (uniqueKey) {
    const duplicate = table === "execution_orders"
      ? await ctx.db.query(table).withIndex("by_unique", q => q.eq("uniqueKey", uniqueKey)).unique()
      : await ctx.db.query(table).withIndex("by_owner_unique", q => q.eq("owner", owner).eq("uniqueKey", uniqueKey)).unique();
    if (duplicate && duplicate.externalId !== externalId) throw new ConvexError("Duplicate record key");
  }
  const status = text(row, "status") || text(row, "state");
  const fields = { externalId, owner, relation, status, uniqueKey,
    created: time(row.created_at ?? row.started_at), time: time(row.scheduled_for ?? row.entry_at ?? row.started_at ?? row.created_at),
    rowJson: JSON.stringify(row) };
  if (existing) await ctx.db.patch(existing._id, fields);
  else await ctx.db.insert(table, fields);
  if (table === "strategies" || table === "automation_agent_runs") {
    const scope = table === "strategies" ? "strategies" : "automation";
    const signal = await ctx.db.query("signals").withIndex("by_scope_entity", q => q.eq("scope", scope).eq("entityId", externalId)).unique();
    const outcome = text(row, "outcome");
    if (!signal || signal.status !== status || signal.outcome !== outcome || Date.now() - signal.updatedAt >= 1000) {
      const value: Omit<Doc<"signals">, "_id" | "_creationTime"> = { userId: owner, scope, entityId: externalId, status, outcome, updatedAt: Date.now() };
      if (signal) await ctx.db.patch(signal._id, value); else await ctx.db.insert("signals", value);
    }
  }
  if (table === "strategies" && ["completed", "cancelled"].includes(status)) {
    const slots = await ctx.db.query("strategy_capital_slots").withIndex("by_relation", q => q.eq("relation", externalId)).collect();
    for (const slot of slots) await put(ctx, "strategy_capital_slots", {
      ...parseRow(slot.rowJson), status: "available", strategy_id: null, proposal_id: null,
      reserved_at: null, released_at: new Date().toISOString(),
    }, slot);
  }
  return JSON.stringify(row);
}

const condition = v.object({ field: v.string(), op: v.union(v.literal("eq"), v.literal("neq"), v.literal("in"),
  v.literal("gt"), v.literal("gte"), v.literal("lt"), v.literal("lte"), v.literal("is"), v.literal("not.is")), valueJson: v.string() });
type Condition = Infer<typeof condition>;
function matches(row: Row, conditions: Condition[]) {
  return conditions.every(({ field, op, valueJson }) => {
    let actual: unknown = row[field] ?? null;
    let expected: unknown = JSON.parse(valueJson);
    if (op === "is") return actual === expected;
    if (op === "not.is") return actual !== expected;
    if (op === "in") return Array.isArray(expected) && expected.includes(actual);
    if (op === "eq") return String(actual) === String(expected);
    if (op === "neq") return String(actual) !== String(expected);
    if (actual === null || expected === null) return false;
    if (field.endsWith("_at") || field === "scheduled_for" || field === "activation_time") {
      actual = time(actual); expected = time(expected);
    }
    if ((typeof actual !== "string" && typeof actual !== "number") || typeof expected !== typeof actual) return false;
    if (typeof actual === "number" && typeof expected === "number") {
      return op === "gt" ? actual > expected : op === "gte" ? actual >= expected : op === "lt" ? actual < expected : actual <= expected;
    }
    if (typeof actual === "string" && typeof expected === "string") {
      return op === "gt" ? actual > expected : op === "gte" ? actual >= expected : op === "lt" ? actual < expected : actual <= expected;
    }
    return false;
  });
}

export const select = query({
  args: { secret: v.string(), table: runtimeTableName, conditions: v.array(condition), paginationOpts: paginationOptsValidator },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const equal = (field: string) => args.conditions.find(item => item.field === field && item.op === "eq");
    const id = equal("id");
    if (id) {
      const record = await get(ctx, args.table, String(JSON.parse(id.valueJson)));
      return { page: record && matches(parseRow(record.rowJson), args.conditions) ? [record.rowJson] : [], isDone: true, continueCursor: "" };
    }
    const owner = equal("user_id");
    const status = equal("status");
    const relation = equal("execution_id") || equal("strategy_id") || equal("strategy_proposal_id");
    const unique = equal("client_order_id");
    const base = ctx.db.query(args.table);
    const indexed = relation ? base.withIndex("by_relation", q => q.eq("relation", String(JSON.parse(relation.valueJson))))
      : unique ? base.withIndex("by_unique", q => q.eq("uniqueKey", String(JSON.parse(unique.valueJson))))
      : owner && status ? base.withIndex("by_owner_status_created", q => q.eq("owner", String(JSON.parse(owner.valueJson))).eq("status", String(JSON.parse(status.valueJson))))
      : owner ? base.withIndex("by_owner_created", q => q.eq("owner", String(JSON.parse(owner.valueJson))))
      : status ? base.withIndex("by_status_created", q => q.eq("status", String(JSON.parse(status.valueJson))))
      : base.withIndex("by_external");
    const page = await indexed.paginate(args.paginationOpts);
    return { ...page, page: page.page.filter(item => matches(parseRow(item.rowJson), args.conditions)).map(item => item.rowJson) };
  },
});

export const write = mutation({
  args: { secret: v.string(), table: runtimeTableName, rowJson: v.string(), conflict: v.optional(v.string()), importOnly: v.optional(v.boolean()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    const row = parseRow(args.rowJson);
    let existing = await get(ctx, args.table, text(row, "id") || text(row, "user_id"));
    if (!existing && args.conflict) {
      const key = String(row[args.conflict]);
      const owner = text(row, "user_id");
      if (owner) existing = await ctx.db.query(args.table).withIndex("by_owner_unique", q => q.eq("owner", owner).eq("uniqueKey", key)).unique();
      else if (args.table === "execution_orders") {
        const parent = await get(ctx, "executions", text(row, "execution_id"));
        if (!parent) throw new ConvexError("Execution parent missing");
        existing = await ctx.db.query(args.table).withIndex("by_owner_unique", q => q.eq("owner", parent.owner).eq("uniqueKey", key)).unique();
      }
    }
    if (args.importOnly) {
      if (process.env.CONVEX_IMPORT_ENABLED !== "true") throw new ConvexError("Import disabled");
      if (existing) {
        const current = parseRow(existing.rowJson);
        if (Object.entries(row).some(([key, value]) => JSON.stringify(current[key]) !== JSON.stringify(value))) throw new ConvexError("Import conflicts with current data");
        return [existing.rowJson];
      }
    } else if (existing && !args.conflict) throw new ConvexError("Record already exists");
    return [await put(ctx, args.table, existing ? { ...parseRow(existing.rowJson), ...row, id: existing.externalId, created_at: parseRow(existing.rowJson).created_at } : row, existing)];
  },
});

export const update = mutation({
  args: { secret: v.string(), table: runtimeTableName, ids: v.array(v.string()), conditions: v.array(condition), patchJson: v.string(), remove: v.optional(v.boolean()) },
  handler: async (ctx, args) => {
    authorizeTradingService(args.secret);
    if (args.ids.length > 100) throw new ConvexError("Mutation batch too large");
    const patch = parseRow(args.patchJson);
    if ("id" in patch || "user_id" in patch) throw new ConvexError("Identity cannot be patched");
    const output = [];
    for (const id of args.ids) {
      const existing = await get(ctx, args.table, id);
      if (!existing) continue;
      const row = parseRow(existing.rowJson);
      if (!matches(row, args.conditions)) continue;
      if (args.remove) {
        if (args.table === "strategies" && ["active", "executing_entry", "executing_exit"].includes(existing.status)) throw new ConvexError("Cannot delete live execution");
        await ctx.db.delete(existing._id);
        output.push(existing.rowJson);
      } else output.push(await put(ctx, args.table, { ...row, ...patch, updated_at: new Date().toISOString() }, existing));
    }
    return output;
  },
});
