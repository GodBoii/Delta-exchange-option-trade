import type { TradeItem } from "@/lib/app-types";
import { toNumber } from "@/lib/format";
import { settledSeries, type PnlPoint } from "@/lib/pnl-series";
import type { ReportAsset } from "@/lib/reporting";

export const ANALYTICS_ZONE = "Asia/Kolkata";
const IST_OFFSET = 5.5 * 60 * 60 * 1000;

/** Use the recorded name exactly; renamed or versioned strategies must not merge silently. */
export function filterStrategyTrades(trades: readonly TradeItem[], asset: ReportAsset, name: string | null) {
  // The server ledger treats pre-ETH rows with a null asset as BTC in its SQL filters.
  return trades.map<TradeItem>(trade => trade.asset === null ? { ...trade, asset: "BTC" } : trade)
    .filter(trade => (asset === "all" || trade.asset === asset) && (name === null || trade.name === name));
}

export function strategyMetrics(trades: readonly TradeItem[]) {
  const points = settledSeries(trades);
  const included = new Set(points.map(point => point.runId));
  let fees = 0;
  let missingFees = 0;
  for (const trade of trades) {
    if (!included.has(trade.runId)) continue;
    const fee = toNumber(trade.exchangeFees);
    if (fee === null) missingFees += 1;
    else fees += fee;
  }
  const gains = points.reduce((sum, point) => sum + Math.max(point.pnl, 0), 0);
  const losses = points.reduce((sum, point) => sum + Math.min(point.pnl, 0), 0);
  const wins = points.filter(point => point.pnl > 0).length;
  const lost = points.filter(point => point.pnl < 0).length;
  const net = gains + losses;
  let peak = 0;
  let maxDrawdown = 0;
  const drawdown: PnlPoint[] = points.map(point => {
    peak = Math.max(peak, point.cumulative);
    const cumulative = point.cumulative - peak;
    maxDrawdown = Math.max(maxDrawdown, -cumulative);
    return { ...point, cumulative };
  });
  return {
    points, drawdown, net, gains, losses, fees, missingFees, wins, lost,
    settled: points.length, excluded: trades.length - points.length,
    even: points.length - wins - lost,
    winRate: points.length ? wins / points.length : null,
    average: points.length ? net / points.length : null,
    profitFactor: losses < 0 ? gains / -losses : null,
    maxDrawdown,
  };
}

/** Heatmaps use settlement time, consistently in IST regardless of the browser timezone. */
export function settlementHeatmaps(points: readonly PnlPoint[]) {
  const days = new Map<string, { net: number; runs: number }>();
  const hours = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => ({ net: 0, runs: 0 })));
  for (const point of points) {
    const date = new Date(point.at + IST_OFFSET);
    const key = date.toISOString().slice(0, 10);
    const day = days.get(key) ?? { net: 0, runs: 0 };
    day.net += point.pnl;
    day.runs += 1;
    days.set(key, day);
    const hour = hours[(date.getUTCDay() + 6) % 7][date.getUTCHours()];
    hour.net += point.pnl;
    hour.runs += 1;
  }
  return { days, hours };
}

/** Quote every field and neutralize spreadsheet formulas in user-controlled strategy names. */
export function strategyCsv(trades: readonly TradeItem[]) {
  const quote = (value: string, userControlled: boolean) => `"${(userControlled && /^\s*[=+\-@]/.test(value) ? "'" : "") + value.replaceAll('"', '""')}"`;
  const rows = trades.map(trade => [trade.runId, trade.name, trade.asset ?? "Unknown", trade.accountingState,
    trade.exitExecutedAt ?? trade.activityAt, trade.realizedPnl ?? "", trade.exchangeFees ?? "",
    trade.deletedByUserAt ?? ""]);
  return [["Run ID", "Strategy", "Asset", "Accounting state", "Settlement/activity UTC", "Net P&L USD", "Fees USD", "Deleted at UTC"], ...rows]
    .map(row => row.map((value, index) => quote(value, index === 1)).join(",")).join("\r\n");
}
