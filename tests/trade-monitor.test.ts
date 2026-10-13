import { describe, expect, it } from "vitest";
import type { TradeMonitorSnapshot } from "@/lib/app-types";
import { monitorFinished, monitorMessage, riskObservationStale } from "@/lib/trade-monitor";

function snapshot(status: string, riskState: Record<string, unknown> = {}): TradeMonitorSnapshot {
  return {
    success: true, source: "live", observedAt: "2026-10-13T10:00:00Z", privateStream: "live", quotes: [],
    run: { id: "run", name: "Trade", status, createdAt: "", entryAt: "", exitAt: "", definition: {},
      capitalPolicy: {}, riskState, settlement: {}, executions: [], orders: [] }
  };
}

describe("trade monitor", () => {
  it("keeps monitoring active, scheduled, and attention runs", () => {
    for (const status of ["active", "scheduled", "attention", "executing_entry", "executing_exit"]) {
      expect(monitorFinished(snapshot(status))).toBe(false);
    }
    for (const status of ["completed", "cancelled", "skipped"]) expect(monitorFinished(snapshot(status))).toBe(true);
    expect(monitorFinished({ ...snapshot("active"), source: "archive" })).toBe(true);
  });
  it("marks missing, invalid, and old engine valuations stale", () => {
    const now = Date.parse("2026-10-13T10:00:00Z");
    for (const pricedAt of [undefined, "invalid", "2026-10-13T09:59:29Z"]) {
      expect(riskObservationStale(snapshot("active", { pricedAt }), now)).toBe(true);
    }
    expect(riskObservationStale(snapshot("active", { pricedAt: "2026-10-13T09:59:50Z" }), now)).toBe(false);
  });
  it("explains closed exposure separately from an active status", () => {
    expect(monitorMessage(snapshot("active", { exposureStatus: "flat" }))).toContain("exposure is closed");
    expect(monitorMessage({ ...snapshot("completed"), source: "archive" })).toContain("Archived record");
    expect(monitorMessage(snapshot("executing_exit"))).toContain("closing and verifying");
  });
});
