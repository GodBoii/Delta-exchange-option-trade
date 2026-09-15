import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";
import { orderContext, orderOutcome } from "./orderValidators";
import { exchangeFill } from "./fillValidators";
import { savedStrategyRecord, capitalRecord, profileRecord, connectionRecord } from "./applicationValidators";
import { runtimeTables } from "./runtimeTables";

export default defineSchema({
  ...runtimeTables,
  profiles: defineTable(profileRecord).index("by_user", ["id"]),
  exchangeConnections: defineTable(connectionRecord).index("by_user", ["user_id"]),
  savedStrategies: defineTable({ ...savedStrategyRecord.fields, deleted: v.boolean() })
    .index("by_external_id", ["id"])
    .index("by_owner_deleted", ["user_id", "deleted"]),
  capitalSettings: defineTable(capitalRecord)
    .index("by_user", ["user_id"]),
  exchangeFills: defineTable({ accountId: v.string(), ...exchangeFill.fields })
    .index("by_account_fill", ["accountId", "fillId"])
    .index("by_account_product_time", ["accountId", "productId", "occurredAt"]),
  productClaims: defineTable({
    accountId: v.string(), productId: v.string(), strategyId: v.string(),
  }).index("by_account_product", ["accountId", "productId"])
    .index("by_account_strategy", ["accountId", "strategyId"]),
  orderIntents: defineTable({
    accountId: v.string(),
    clientOrderId: v.string(),
    payload: v.string(),
    context: v.optional(orderContext),
    materialized: v.optional(v.boolean()),
    outcome: orderOutcome,
    unresolved: v.boolean(),
    updatedAt: v.number(),
  })
    .index("by_account_client", ["accountId", "clientOrderId"])
    .index("by_account_strategy", ["accountId", "context.strategyId"])
    .index("by_account_unresolved", ["accountId", "unresolved"]),
  signals: defineTable({
    userId: v.string(),
    scope: v.union(v.literal("automation"), v.literal("strategies")),
    entityId: v.string(),
    status: v.string(),
    outcome: v.optional(v.string()),
    updatedAt: v.number(),
  })
    .index("by_scope_entity", ["scope", "entityId"])
    .index("by_user_scope_updated", ["userId", "scope", "updatedAt"]),
});
