import { v } from "convex/values";

export const allocationMode = v.union(
  v.literal("full_balance"), v.literal("half_balance"), v.literal("one_third_balance"),
  v.literal("one_quarter_balance"), v.literal("fixed_amount"),
);

export const savedStrategyRecord = v.object({
  id: v.string(), user_id: v.union(v.string(), v.null()), name: v.string(), definitionJson: v.string(),
  source_run_id: v.union(v.string(), v.null()), version: v.number(), enabled_for_ai: v.boolean(),
  created_at: v.string(), updated_at: v.string(),
});

export const capitalRecord = v.object({
  user_id: v.string(), allocation_mode: allocationMode, capital_amount: v.union(v.string(), v.null()),
});
