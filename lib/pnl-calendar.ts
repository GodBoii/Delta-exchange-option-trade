import type { PnlCalendarResponse, PnlDay } from "@/lib/app-types";

const DAY_MS = 86_400_000;

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid daily P&L response.");
  return Object.fromEntries(Object.entries(value));
}

function dateString(value: unknown): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("Invalid calendar date.");
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) {
    throw new Error("Invalid calendar date.");
  }
  return value;
}

function money(value: unknown): string {
  if (typeof value !== "string" || !/^-?\d+(?:\.\d+)?$/.test(value) || !Number.isFinite(Number(value))) {
    throw new Error("Invalid daily P&L amount.");
  }
  return value;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid daily trade count.");
  return value;
}

/** Validate the new calendar boundary before building dates or financial labels. */
export function parsePnlCalendar(value: unknown): PnlCalendarResponse {
  const data = record(value);
  const { range, asset, strategy, timezone, asOf, historyComplete, historyVerifiedAt } = data;
  if (data.success !== true || (range !== "7d" && range !== "30d" && range !== "90d" && range !== "1y" && range !== "all")
    || (asset !== "all" && asset !== "BTC" && asset !== "ETH")
    || (strategy !== null && typeof strategy !== "string") || timezone !== "Asia/Kolkata"
    || typeof asOf !== "string" || !Number.isFinite(Date.parse(asOf))
    || typeof historyComplete !== "boolean" || (historyVerifiedAt !== null && typeof historyVerifiedAt !== "string")
    || !Array.isArray(data.days)) throw new Error("Invalid daily P&L response. Refresh after the backend is updated.");
  const startDate = data.startDate === null ? null : dateString(data.startDate);
  const endDate = dateString(data.endDate);
  if ((startDate !== null && startDate > endDate) || (range !== "all" && startDate === null)) {
    throw new Error("Invalid calendar period.");
  }
  const seen = new Set<string>();
  const days = data.days.map(value => {
    const row = record(value);
    const date = dateString(row.date);
    if (seen.has(date) || date > endDate || (startDate !== null && date < startDate)) throw new Error("Invalid settlement day.");
    seen.add(date);
    const day = { date, netRealizedPnl: money(row.netRealizedPnl), grossGains: money(row.grossGains),
      grossLosses: money(row.grossLosses), exchangeFees: money(row.exchangeFees), settledRuns: count(row.settledRuns),
      wins: count(row.wins), losses: count(row.losses), breakEven: count(row.breakEven) };
    if (day.settledRuns !== day.wins + day.losses + day.breakEven) throw new Error("Invalid daily outcome counts.");
    return day;
  }).sort((a, b) => a.date.localeCompare(b.date));
  return { success: true, range, asset, strategy, timezone, asOf, startDate, endDate, historyComplete, historyVerifiedAt, days };
}

/** Calendar arithmetic uses UTC dates; dates arriving from the server are already IST days. */
export function calendarDates(start: string, end: string): string[] {
  const first = Date.parse(`${start}T00:00:00Z`);
  const last = Date.parse(`${end}T00:00:00Z`);
  if (!Number.isFinite(first) || !Number.isFinite(last) || last < first) return [];
  const weekday = (new Date(first).getUTCDay() + 6) % 7;
  const paddedStart = first - weekday * DAY_MS;
  const count = Math.ceil(((last - paddedStart) / DAY_MS + 1) / 7) * 7;
  if (count > 378) return [];
  return Array.from({ length: count }, (_, index) => new Date(paddedStart + index * DAY_MS).toISOString().slice(0, 10));
}

export function dayTone(day: PnlDay | undefined, maximum: number): string {
  if (!day) return "empty";
  const net = Number(day.netRealizedPnl);
  if (net === 0) return "even";
  const ratio = maximum > 0 ? Math.abs(net) / maximum : 0;
  const level = ratio <= 0.25 ? 1 : ratio <= 0.5 ? 2 : ratio <= 0.75 ? 3 : 4;
  return `${net > 0 ? "gain" : "loss"}-${level}`;
}

export function calendarYears(days: readonly PnlDay[], endDate: string): number[] {
  const latest = Number(endDate.slice(0, 4));
  const earliest = days.length ? Math.min(latest, ...days.map(day => Number(day.date.slice(0, 4)))) : latest;
  return Array.from({ length: latest - earliest + 1 }, (_, index) => latest - index);
}

export function calendarDateLabel(date: string): string {
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC"
  }).format(new Date(`${date}T00:00:00Z`));
}
