import { ConvexError } from "convex/values";
import { parseRow, type Row } from "./runtimeRecords";

/** Only expiry resolution and scheduling may change a catalog definition. */
export function validateMaterializedDefinition(source: Row, materialized: Row) {
  const equal = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  const mutable = ["entry", "legs", "acknowledgement", "selectionCriteria", "allocationMode", "capitalAmount"];
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
