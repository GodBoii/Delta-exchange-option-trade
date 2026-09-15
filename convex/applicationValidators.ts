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

export const profileRecord = v.object({
  id: v.string(), display_name: v.union(v.string(), v.null()), avatar_url: v.union(v.string(), v.null()),
  phone_number: v.union(v.string(), v.null()), created_at: v.string(), updated_at: v.string(),
});

export const connectionRecord = v.object({
  id: v.string(), user_id: v.string(), delta_user_id: v.string(), account_name: v.string(),
  email_masked: v.union(v.string(), v.null()), environment: v.literal("production"),
  status: v.union(v.literal("connected"), v.literal("revoked")),
  ciphertext: v.union(v.string(), v.null()), fingerprint: v.string(), updated_at: v.string(),
});
