import { v } from "convex/values";

export const orderContext = v.object({
  strategyId: v.string(), executionId: v.string(), legId: v.string(),
  contractValue: v.optional(v.string()), referencePrice: v.optional(v.string()),
});

export const orderOutcome = v.union(
  v.object({ kind: v.literal("unknown") }),
  v.object({ kind: v.literal("accepted"), response: v.string() }),
  v.object({ kind: v.literal("rejected"), code: v.string(), message: v.string() }),
);
