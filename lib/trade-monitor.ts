import type { TradeMonitorSnapshot } from "@/lib/app-types";

export const MONITOR_REFRESH_MS = 2_000;
export const MONITOR_STALE_MS = 30_000;

export function monitorFinished(snapshot: TradeMonitorSnapshot): boolean {
  return snapshot.source === "archive" || ["completed", "cancelled", "skipped"].includes(snapshot.run.status);
}

export function monitorMessage(snapshot: TradeMonitorSnapshot): string {
  if (snapshot.source === "archive") return "Archived record. This run was removed from the user's history.";
  const run = snapshot.run;
  if (run.riskState.exposureStatus === "flat") return "Delta exposure is closed. Final accounting may still be reconciling.";
  switch (run.status) {
    case "draft": return "Draft saved. No entry is scheduled.";
    case "scheduled": return "Waiting for the scheduled entry and entry checks.";
    case "executing_entry": return "Entry is in progress. Orders and fills update below.";
    case "executing_exit": return "Exit is in progress. The engine is closing and verifying exposure.";
    case "active": return "Trade is active. The engine continues enforcing its existing exit and risk rules.";
    case "attention": return "This run needs attention. Review the latest error and recorded orders.";
    case "completed": return "Trade completed. Final recorded results are shown below.";
    case "cancelled": return "Run cancelled. Review recorded fills for any earlier execution.";
    case "skipped": return "Entry was skipped. Review the recorded outcome in Details.";
    default: return "Review the current recorded state and execution activity below.";
  }
}

export function riskObservationStale(snapshot: TradeMonitorSnapshot, now: number): boolean {
  const pricedAt = snapshot.run.riskState.pricedAt;
  const timestamp = typeof pricedAt === "string" ? Date.parse(pricedAt) : NaN;
  return !Number.isFinite(timestamp) || now - timestamp > MONITOR_STALE_MS;
}
