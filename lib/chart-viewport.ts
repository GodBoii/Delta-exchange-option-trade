export const DEFAULT_VISIBLE = 80;
export const MIN_VISIBLE = 10;

export type CandleRange = { start: number; end: number };
type TimedCandle = { openTime: number };
export type ChartViewport =
  | { kind: "latest"; count: number }
  | { kind: "history"; count: number; firstTime: number }
  | { kind: "all" };

export function clampRange(start: number, count: number, total: number): CandleRange {
  if (total <= 0) return { start: 0, end: 0 };
  const size = Math.min(total, Math.max(MIN_VISIBLE, Math.round(count)));
  const first = Math.max(0, Math.min(total - size, Math.round(start)));
  return { start: first, end: first + size };
}

export function resolveViewport(viewport: ChartViewport, candles: readonly TimedCandle[]): CandleRange {
  const total = candles.length;
  if (viewport.kind === "all") return { start: 0, end: total };
  if (viewport.kind === "latest") return clampRange(total - viewport.count, viewport.count, total);
  // Candle indexes shift when the rolling feed drops its oldest candle.
  const index = candles.findIndex(candle => candle.openTime >= viewport.firstTime);
  return clampRange(index < 0 ? total : index, viewport.count, total);
}

export function viewportFromRange(range: CandleRange, candles: readonly TimedCandle[]): ChartViewport {
  const next = clampRange(range.start, range.end - range.start, candles.length);
  const count = next.end - next.start;
  if (count === candles.length) return { kind: "all" };
  const first = candles[next.start];
  if (next.end === candles.length || !first) return { kind: "latest", count };
  return { kind: "history", count, firstTime: first.openTime };
}

export function zoomRange(range: CandleRange, factor: number, anchorRatio: number, total: number): CandleRange {
  const ratio = Math.max(0, Math.min(1, anchorRatio));
  const count = range.end - range.start;
  const nextCount = Math.min(total, Math.max(MIN_VISIBLE, Math.round(count * factor)));
  return clampRange(range.start + ratio * (count - nextCount), nextCount, total);
}

export function moveRange(range: CandleRange, shift: number, total: number): CandleRange {
  return clampRange(range.start + shift, range.end - range.start, total);
}

export function resizeRange(range: CandleRange, edge: "left" | "right", shift: number, total: number): CandleRange {
  const minimum = Math.min(MIN_VISIBLE, total);
  return edge === "left"
    ? { start: Math.max(0, Math.min(range.end - minimum, Math.round(range.start + shift))), end: range.end }
    : { start: range.start, end: Math.min(total, Math.max(range.start + minimum, Math.round(range.end + shift))) };
}

export function plotRatio(clientX: number, left: number, width: number, viewWidth: number, paddingLeft: number, paddingRight: number) {
  if (width <= 0) return 0;
  return Math.max(0, Math.min(1, ((clientX - left) / width * viewWidth - paddingLeft) / (viewWidth - paddingLeft - paddingRight)));
}
