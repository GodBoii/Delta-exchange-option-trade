import { ConvexError } from "convex/values";
import { parseRow, type Row } from "./runtimeRecords";

/** The agent may select a holding period and expiry; risk and leg structure stay fixed. */
export function validateMaterializedDefinition(source: Row, materialized: Row) {
  const equal = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  const mutable = ["entry", "legs", "holdingMode", "expiryPolicy", "acknowledgement", "selectionCriteria", "allocationMode", "capitalAmount"];
  if (materialized.holdingMode !== undefined && !["intraday", "hold_to_expiry"].includes(String(materialized.holdingMode))) throw new ConvexError("Invalid holding mode");
  if (materialized.expiryPolicy !== undefined && !["same_day", "next_day", "7_day", "30_day"].includes(String(materialized.expiryPolicy))) throw new ConvexError("Invalid expiry policy");
  if (materialized.entry !== undefined) {
    const entry = parseRow(JSON.stringify(materialized.entry));
    const original = source.entry === undefined ? {} : parseRow(JSON.stringify(source.entry));
    if (entry.strategyType !== undefined && !["intraday", "btst", "positional"].includes(String(entry.strategyType))) throw new ConvexError("Invalid strategy timeframe");
    for (const key of new Set([...Object.keys(original), ...Object.keys(entry)])) {
      if (!["entryAt", "exitAt", "strategyType"].includes(key) && !equal(original[key], entry[key])) throw new ConvexError("AI proposal changed entry configuration");
    }
  }
  for (const key of new Set([...Object.keys(source), ...Object.keys(materialized)])) {
    if (!mutable.includes(key) && !equal(source[key], materialized[key])) throw new ConvexError("AI proposal changed a strategy-owned field");
  }
  if (!Array.isArray(source.legs) || !Array.isArray(materialized.legs) || source.legs.length !== materialized.legs.length) throw new ConvexError("AI proposal changed strategy legs");
  for (let index = 0; index < source.legs.length; index++) {
    const original = parseRow(JSON.stringify(source.legs[index]));
    const proposed = parseRow(JSON.stringify(materialized.legs[index]));
    for (const key of new Set([...Object.keys(original), ...Object.keys(proposed)])) {
      if (key !== "expiry" && !equal(original[key], proposed[key])) throw new ConvexError("AI proposal changed leg configuration");
    }
  }
}
