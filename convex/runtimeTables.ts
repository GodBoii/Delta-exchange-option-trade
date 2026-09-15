import { defineTable } from "convex/server";
import { v } from "convex/values";

export const runtimeTableName = v.union(
  v.literal("strategies"), v.literal("executions"), v.literal("execution_orders"),
  v.literal("strategy_capital_slots"), v.literal("strategy_proposals"),
  v.literal("automation_settings"), v.literal("automation_agent_runs"), v.literal("automation_market_snapshots"),
);

function recordTable() {
  return defineTable({
    externalId: v.string(), owner: v.string(), status: v.string(), relation: v.string(), uniqueKey: v.string(),
    created: v.number(), time: v.number(), rowJson: v.string(),
  }).index("by_external", ["externalId"])
    .index("by_owner_unique", ["owner", "uniqueKey"])
    .index("by_unique", ["uniqueKey"])
    .index("by_relation", ["relation"])
    .index("by_owner_created", ["owner", "created"])
    .index("by_owner_status_created", ["owner", "status", "created"])
    .index("by_status_created", ["status", "created"])
    .index("by_owner_time", ["owner", "time"])
    .index("by_status_time", ["status", "time"]);
}

export const runtimeTables = {
  strategies: recordTable(), executions: recordTable(), execution_orders: recordTable(),
  strategy_capital_slots: recordTable(), strategy_proposals: recordTable(),
  automation_settings: recordTable(), automation_agent_runs: recordTable(), automation_market_snapshots: recordTable(),
};
