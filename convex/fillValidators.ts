import { v } from "convex/values";

export const exchangeFill = v.object({
  fillId: v.string(), productId: v.string(), orderId: v.string(),
  side: v.union(v.literal("buy"), v.literal("sell")),
  quantity: v.string(), price: v.string(), commission: v.union(v.string(), v.null()),
  occurredAt: v.string(),
});
