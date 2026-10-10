import type {
  AccountingState, AgentAsset, CapitalObservation, LiveWallet, ReportRange, StrategyRun, TradeItem
} from "@/lib/app-types";

/** P&L time filters. Values match the backend's `range` parameter. */
export const RANGE_OPTIONS: readonly { value: ReportRange; label: string }[] = [
  // Short labels fit five peers on a phone-width segmented control without truncation.
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
  { value: "90d", label: "90D" },
  { value: "1y", label: "1Y" },
  { value: "all", label: "All" }
];

export type ReportAsset = AgentAsset | "all";

/** Older backends ignore unknown query parameters; never label their combined totals as one market. */
export function verifyReportAsset(response: { asset?: ReportAsset }, requested: ReportAsset): void {
  if (response.asset === requested || (requested === "all" && response.asset === undefined)) return;
  throw new Error(`The server did not return ${requested === "all" ? "combined" : requested} results. Refresh after the backend is updated.`);
}

export const REPORT_ASSET_OPTIONS: readonly { value: ReportAsset; label: string }[] = [
  { value: "all", label: "All" },
  { value: "BTC", label: "BTC" },
  { value: "ETH", label: "ETH" }
];

/** Reject stale backend deployments that silently ignore a requested strategy. */
export function verifyReportStrategy(response: { strategy?: string | null }, requested: string | null): void {
  if ((response.strategy ?? null) === requested) return;
  throw new Error("The server did not return the selected strategy. Refresh after the backend is updated.");
}

export function pnlQuery(params: { range: ReportRange; asset: ReportAsset; strategy?: string | null }): string {
  const query = new URLSearchParams({ range: params.range });
  if (params.asset !== "all") query.set("asset", params.asset);
  if (params.strategy != null) query.set("strategy", params.strategy);
  return query.toString();
}

export const STATE_LABELS: Record<AccountingState, string> = {
  settled: "Settled",
  open: "Open",
  scheduled: "Scheduled",
  cancelled: "Cancelled",
  incomplete: "Incomplete",
  attention: "Needs attention"
};

export const STATE_TONES: Record<AccountingState, "positive" | "active" | "neutral" | "warning" | "negative"> = {
  settled: "positive",
  open: "active",
  scheduled: "neutral",
  cancelled: "neutral",
  incomplete: "warning",
  attention: "negative"
};

/** Why a run is not counted, in words an account holder can act on. */
export function exclusionText(reason: string | null): string | null {
  switch (reason) {
    case null: return null;
    case "position_open": return "Position still open. Premium received is not profit.";
    case "needs_attention": return "Needs attention before it can be counted.";
    case "position_not_fully_closed": return "Not every lot was closed.";
    case "fill_state_unknown": return "An order's fill state is unknown.";
    case "fees_pending": return "Final exchange fees are still pending.";
    case "contract_value_missing": return "Contract size was not recorded, so P&L cannot be priced.";
    case "realized_pnl_missing": return "No realized P&L was recorded.";
    case "no_fills": return "No orders filled.";
    case "cancelled_after_fill": return "Cancelled after a fill; accounting is incomplete.";
    case "entry_not_placed": return "Entry was not placed. See the run for its recorded reason.";
    case "skipped_after_fill": return "A skipped run has recorded fills; accounting needs review.";
    default: return "Accounting is incomplete.";
  }
}

export function winRateText(rate: number | null): string {
  return rate === null ? "No settled runs" : `${(rate * 100).toFixed(1)}%`;
}

export function tradeQuery(params: {
  range: ReportRange;
  state: AccountingState | "all";
  asset?: ReportAsset;
  strategy?: string | null;
  cursor?: string | null;
  deleted?: "include" | "exclude" | "only";
  limit?: number;
}): string {
  const query = new URLSearchParams({ range: params.range, limit: String(params.limit ?? 25) });
  if (params.state !== "all") query.set("state", params.state);
  if (params.asset && params.asset !== "all") query.set("asset", params.asset);
  if (params.strategy != null) query.set("strategy", params.strategy);
  if (params.deleted) query.set("deleted", params.deleted);
  if (params.cursor) query.set("cursor", params.cursor);
  return query.toString();
}

/** The run fields the detail dialog needs before the full record loads. */
export function runStub(trade: TradeItem): StrategyRun {
  return {
    id: trade.runId,
    name: trade.name,
    status: trade.status,
    entryAt: trade.entryAt ?? trade.createdAt,
    exitAt: trade.exitAt ?? trade.createdAt,
    entryExecutedAt: trade.entryExecutedAt,
    exitExecutedAt: trade.exitExecutedAt,
    createdAt: trade.createdAt
  };
}

export function observationLabel(observation: CapitalObservation): string {
  if (observation.kind === "run_allocation") return "Budget allocated to one run";
  if (observation.kind === "policy") {
    return observation.source === "capital_policy" ? "Capital policy changed" : "Capital policy at run entry";
  }
  return observation.source === "live_wallet" ? "Wallet balance read from Delta" : "Wallet balance at run entry";
}

export function walletUnavailableText(wallet: LiveWallet): string | null {
  if (wallet.state === "not_connected") return "Delta is not connected, so there is no live balance.";
  if (wallet.state === "unavailable") return "Delta did not answer. The live balance is unavailable right now.";
  return null;
}

export function allocationModeText(mode: string | null): string {
  switch (mode) {
    case null: return "Not set";
    case "full_balance": return "Full balance per run";
    case "half_balance": return "Half balance per run";
    case "one_third_balance": return "One third per run";
    case "one_quarter_balance": return "One quarter per run";
    case "fixed_amount": return "Fixed amount per run";
    default: return mode.replaceAll("_", " ");
  }
}
