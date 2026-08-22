/**
 * Single source of truth for number, currency, and time formatting.
 *
 * Trading surfaces are read by scanning columns, so every numeric formatter
 * returns a fixed-precision string and the stylesheet pairs them with tabular
 * numerals. Formatters never invent precision: values that are missing stay
 * visibly missing (`EM_DASH`) instead of rendering as a confident zero.
 */

export const EM_DASH = "—";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 1 });
const usdCompact = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 });
const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });

export function isNumeric(value: unknown): value is number | string {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string" && value.trim() !== "") return Number.isFinite(Number(value));
  return false;
}

/** Delta returns most quantities as strings; coerce defensively. */
export function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function money(value: number) {
  return usd.format(value);
}

export function signedMoney(value: number) {
  return `${value >= 0 ? "+" : "-"}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 1 })}`;
}

export function currencyCompact(value: number) {
  return usdCompact.format(value);
}

export function signedCurrencyCompact(value: number) {
  return `${value >= 0 ? "+" : "-"}${usdCompact.format(Math.abs(value))}`;
}

export function compact(value: number) {
  return compactNumber.format(value);
}

export function percent(value: number, digits = 2) {
  return `${value.toFixed(digits)}%`;
}

export function signedPercent(value: number, digits = 3) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

/** Chart axis and readout price: one decimal keeps candle columns aligned. */
export function price(value: number) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** Order-book price: two decimals, because the tick size is sub-dollar. */
export function bookPrice(value: number) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Asset quantities keep enough precision to stay meaningful for BTC-sized units. */
export function quantity(value: number, digits = 4) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function humanize(value: string) {
  return value.replaceAll("_", " ");
}

export function titleCase(value: string) {
  return humanize(value).replace(/\b\w/g, character => character.toUpperCase());
}

const dayMonthYear = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "2-digit" });
const dayMonthTime = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const clock = new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const fullTimestamp = new Intl.DateTimeFormat("en-IN", {
  day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
});

/** `YYYY-MM-DD` expiry input value rendered for humans. */
export function formatExpiry(value: string) {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? "No expiry" : dayMonthYear.format(date);
}

export function formatDateTime(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return EM_DASH;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? EM_DASH : dayMonthTime.format(date);
}

/**
 * Audit-grade timestamp: full date plus seconds. Used wherever a user has to
 * reason about execution timing rather than scan a column.
 */
export function formatTimestamp(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return EM_DASH;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? EM_DASH : fullTimestamp.format(date);
}

/** Fixed-precision decimal for recorded prices and quantities. */
export function decimal(value: unknown, digits = 2) {
  const parsed = toNumber(value);
  return parsed === null
    ? EM_DASH
    : parsed.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Signed decimal, so an adverse figure reads as adverse at a glance. */
export function signedDecimal(value: unknown, digits = 2) {
  const parsed = toNumber(value);
  if (parsed === null) return EM_DASH;
  const magnitude = Math.abs(parsed).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${parsed > 0 ? "+" : parsed < 0 ? "-" : ""}${magnitude}`;
}

export function formatClock(value: string | number) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? EM_DASH : clock.format(date);
}

/**
 * Compact relative time for operational logs ("in 4h", "12m ago").
 * Scheduling correctness depends on the direction, so the sign is explicit.
 */
export function relativeTime(value: string | number | null | undefined, now = Date.now()) {
  if (value === null || value === undefined || value === "") return EM_DASH;
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) return EM_DASH;
  const deltaSeconds = Math.round((target - now) / 1000);
  const magnitude = Math.abs(deltaSeconds);
  const [amount, unit] = magnitude < 60 ? [magnitude, "s"]
    : magnitude < 3600 ? [Math.round(magnitude / 60), "m"]
      : magnitude < 86400 ? [Math.round(magnitude / 3600), "h"]
        : [Math.round(magnitude / 86400), "d"];
  if (amount === 0) return "now";
  return deltaSeconds > 0 ? `in ${amount}${unit}` : `${amount}${unit} ago`;
}

/** Duration between two instants, used for strategy schedule windows. */
export function formatDuration(fromValue: string | number, toValue: string | number) {
  const from = new Date(fromValue).getTime();
  const to = new Date(toValue).getTime();
  if (Number.isNaN(from) || Number.isNaN(to) || to <= from) return EM_DASH;
  const minutes = Math.round((to - from) / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days >= 1) return `${days}d ${hours % 24}h`;
  if (hours >= 1) return `${hours}h ${minutes % 60}m`;
  return `${minutes}m`;
}

/** `datetime-local` inputs need a local, timezone-shifted ISO fragment. */
export function toLocalInput(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function toIso(value: string) {
  return value ? new Date(value).toISOString() : "";
}

const TECHNICAL_ERROR_PATTERN = /\b(?:supabase|postgres(?:ql)?|database|migration|docker|backend|localhost|configured ports?|environment variables?|news analyzer|server logs?|trace[_ -]?id|stack trace|vault|json response)\b/i;
const NETWORK_ERROR_PATTERN = /(?:failed to fetch|networkerror|network request failed|connection refused|could not be reached|is unreachable|timed? out|service unavailable)/i;

/**
 * Converts boundary errors into customer-facing copy. Internal detail belongs in
 * server logs, not in notifications or form errors.
 */
export function errorMessage(error: unknown, fallback = "Something went wrong. Please try again.") {
  const message = error instanceof Error ? error.message.trim() : "";
  if (!message) return fallback;
  if (NETWORK_ERROR_PATTERN.test(message)) return "The service is temporarily unavailable. Please try again.";
  if (TECHNICAL_ERROR_PATTERN.test(message)) return fallback;
  return message;
}
