import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
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
