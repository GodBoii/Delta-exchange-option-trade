import { toNumber } from "@/lib/format";
import type { TradeItem } from "@/lib/app-types";

/** One settled run on the equity curve, in USD. */
export type PnlPoint = {
  runId: string;
  name: string;
  /** When the run settled: exit fill time, else its last recorded activity. */
  at: number;
  pnl: number;
  /** Running total of `pnl` up to and including this run. */
  cumulative: number;
};

export type BucketSize = "day" | "week" | "month";

/** Settled results grouped into one bar. Amounts are USD; `losses` is negative or zero. */
export type PnlBucket = {
  start: number;
  gains: number;
  losses: number;
  net: number;
  wins: number;
  lost: number;
  runs: number;
};

const DAY_MS = 86_400_000;

/**
 * Settled runs in settlement order with a running total.
 *
 * Runs that are not settled, or whose realized P&L or time cannot be read, are
 * dropped: the curve must add up to the same net figure as the summary tiles,
 * which only count settled runs.
 */
export function settledSeries(trades: readonly TradeItem[]): PnlPoint[] {
  const rows = trades.flatMap(trade => {
    if (trade.accountingState !== "settled") return [];
    const pnl = toNumber(trade.realizedPnl);
    const at = Date.parse(trade.exitExecutedAt ?? trade.activityAt);
    return pnl === null || Number.isNaN(at) ? [] : [{ runId: trade.runId, name: trade.name, at, pnl }];
  });
  rows.sort((a, b) => a.at - b.at || a.runId.localeCompare(b.runId));
  let cumulative = 0;
  return rows.map(row => {
    cumulative += row.pnl;
    return { ...row, cumulative };
  });
}

/** Daily bars for up to about six weeks of data, weekly up to six months, monthly beyond. */
export function bucketSizeFor(points: readonly PnlPoint[]): BucketSize {
  if (points.length < 2) return "day";
  const span = points[points.length - 1].at - points[0].at;
  if (span <= 45 * DAY_MS) return "day";
  if (span <= 183 * DAY_MS) return "week";
  return "month";
}

/** Start of the local day, Monday-based week, or month containing `at`. */
export function bucketStart(at: number, size: BucketSize): number {
  const date = new Date(at);
  if (size === "month") return new Date(date.getFullYear(), date.getMonth(), 1).getTime();
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  if (size === "week") day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  return day.getTime();
}

/** Groups settled points into bars, oldest first. Break-even runs count as runs but neither win nor loss. */
export function bucketize(points: readonly PnlPoint[], size: BucketSize): PnlBucket[] {
  const buckets = new Map<number, PnlBucket>();
  for (const point of points) {
    const start = bucketStart(point.at, size);
    const bucket = buckets.get(start) ?? { start, gains: 0, losses: 0, net: 0, wins: 0, lost: 0, runs: 0 };
    if (point.pnl > 0) { bucket.gains += point.pnl; bucket.wins += 1; }
    if (point.pnl < 0) { bucket.losses += point.pnl; bucket.lost += 1; }
    bucket.net += point.pnl;
    bucket.runs += 1;
    buckets.set(start, bucket);
  }
  return [...buckets.values()].sort((a, b) => a.start - b.start);
}

/**
 * Evenly spaced axis values covering [min, max], always including zero so the
 * profit and loss sides of a chart are read against the same baseline.
 */
export function niceTicks(min: number, max: number, count = 4): number[] {
  const low = Math.min(min, 0);
  const high = Math.max(max, 0);
  if (low === high) return [0];
  const raw = (high - low) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(factor => factor * magnitude).find(value => value >= raw) ?? raw;
  const ticks: number[] = [];
  for (let value = Math.floor(low / step) * step; value <= high + step * 1e-9; value += step) {
    ticks.push(Math.abs(value) < step * 1e-9 ? 0 : Number(value.toPrecision(12)));
  }
  if (ticks[ticks.length - 1] < high) ticks.push(Number((ticks[ticks.length - 1] + step).toPrecision(12)));
  return ticks;
}
