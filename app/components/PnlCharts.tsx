"use client";

import {
  useCallback, useEffect, useId, useMemo, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode
} from "react";
import { BarChart3, RefreshCw, TrendingUp } from "@/app/components/icons";
import { useCurrency } from "@/app/components/currency";
import { requestJson } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { tradeQuery, verifyReportAsset, type ReportAsset } from "@/lib/reporting";
import {
  bucketSizeFor, bucketize, niceTicks, settledSeries, type BucketSize, type PnlBucket, type PnlPoint
} from "@/lib/pnl-series";
import type { ReportRange, TradeItem, TradePage } from "@/lib/app-types";
import { EmptyState, InlineMessage, Panel, PanelHeader } from "@/app/components/ui";

/** 20 pages of 50 is 1,000 settled runs, far past what one period holds today. */
const PAGE_SIZE = 50;
const MAX_PAGES = 20;

const HEIGHT = 280;
const MARGIN = { top: 14, right: 16, bottom: 30, left: 76 };

type ChartData =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; trades: TradeItem[]; truncated: boolean };

/**
 * Every settled run in the period, read page by page from the same endpoint as
 * the trade table. Older requests are ignored once a newer one starts, and a
 * refresh keeps the current charts on screen until the new data arrives.
 */
function useSettledTrades(range: ReportRange, asset: ReportAsset, refreshToken: number) {
  const [data, setData] = useState<ChartData>({ kind: "loading" });
  const generation = useRef(0);

  const load = useCallback(async () => {
    const current = ++generation.current;
    setData(previous => previous.kind === "ready" ? previous : { kind: "loading" });
    try {
      const trades: TradeItem[] = [];
      let cursor: string | null = null;
      let pages = 0;
      do {
        const query = tradeQuery({ range, asset, state: "settled", limit: PAGE_SIZE, cursor });
        const page: TradePage = await requestJson<TradePage>(`/api/me/trades?${query}`);
        if (current !== generation.current) return;
        verifyReportAsset(page, asset);
        trades.push(...page.items);
        cursor = page.nextCursor;
        pages += 1;
      } while (cursor && pages < MAX_PAGES);
      setData({ kind: "ready", trades, truncated: Boolean(cursor) });
    } catch (error) {
      if (current === generation.current) setData({ kind: "error", message: errorMessage(error) });
    }
  }, [range, asset]);

  useEffect(() => {
    setData({ kind: "loading" });
  }, [range, asset]);

  useEffect(() => { void load(); }, [load, refreshToken]);

  return { data, reload: load };
}

/** Width of the chart's container, re-measured as the layout changes. */
function useMeasuredWidth() {
  const [width, setWidth] = useState(0);
  const ref = useCallback((node: HTMLDivElement | null) => {
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

/** SVG ids must not carry the punctuation React puts in `useId` values. */
function useSvgId(prefix: string) {
  return `${prefix}-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
}

function useAxisFormat() {
  const { currencyCode } = useCurrency();
  return useMemo(() => {
    const format = new Intl.NumberFormat(currencyCode === "INR" ? "en-IN" : "en-US", {
      style: "currency", currency: currencyCode, notation: "compact", maximumFractionDigits: 1
    });
    return (value: number) => format.format(value);
  }, [currencyCode]);
}

function shortDate(at: number) {
  return new Date(at).toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

function bucketLabel(start: number, size: BucketSize, long = false) {
  if (size === "month") {
    return new Date(start).toLocaleDateString(undefined, { month: "short", year: long ? "numeric" : "2-digit" });
  }
  const day = long
    ? new Date(start).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" })
    : shortDate(start);
  return size === "week" && long ? `Week of ${day}` : day;
}

/** Vertical scale from ticks, with a unit span when every value is zero. */
function yScale(ticks: number[], plotHeight: number) {
  const low = ticks[0];
  const high = ticks[ticks.length - 1] === low ? low + 1 : ticks[ticks.length - 1];
  return (value: number) => MARGIN.top + ((high - value) / (high - low)) * plotHeight;
}

/** Arrow keys, Home and End step through `count` items; returns the next index or null if the key is not ours. */
function stepIndex(event: ReactKeyboardEvent, current: number | null, first: number, last: number) {
  const from = current ?? last;
  switch (event.key) {
    case "ArrowLeft": return Math.max(first, from - 1);
    case "ArrowRight": return Math.min(last, from + 1);
    case "Home": return first;
    case "End": return last;
    default: return null;
  }
}

/**
 * P&L charts for the personal report: the running total of settled results,
 * and winnings against losses per day, week or month. Both count only settled
 * runs, so the line ends on the same net figure as the Performance tiles.
 */
export default function PnlCharts({ range, asset, refreshToken }: {
  range: ReportRange; asset: ReportAsset; refreshToken: number;
}) {
  const { data, reload } = useSettledTrades(range, asset, refreshToken);
  const points = useMemo(() => data.kind === "ready" ? settledSeries(data.trades) : [], [data]);
  const size = bucketSizeFor(points);
  const buckets = useMemo(() => bucketize(points, size), [points, size]);
  const { formatMoney } = useCurrency();

  const net = points.length ? points[points.length - 1].cumulative : 0;
  const cadence = size === "day" ? "day" : size === "week" ? "week" : "month";

  return (
    <div className="pnl-charts">
      <Panel className="report-panel pnl-chart-panel">
        <PanelHeader
          icon={<TrendingUp />}
          title="Cumulative P&L"
          meta={data.kind === "ready" && points.length
            ? `${formatMoney(net, { signed: true })} over ${points.length} settled ${points.length === 1 ? "run" : "runs"}`
            : "Running total of settled runs"}
        />
        <ChartBody data={data} empty={!points.length} onRetry={() => void reload()}>
          <LineChart points={points} />
        </ChartBody>
      </Panel>

      <Panel className="report-panel pnl-chart-panel">
        <PanelHeader
          icon={<BarChart3 />}
          title="Winnings and losses"
          meta={`Settled results per ${cadence}`}
          actions={
            <span className="pnl-legend" aria-hidden="true">
              <span className="pnl-legend-item is-gain">Winnings</span>
              <span className="pnl-legend-item is-loss">Losses</span>
            </span>
          }
        />
        <ChartBody data={data} empty={!points.length} onRetry={() => void reload()}>
          <BarChart buckets={buckets} size={size} />
        </ChartBody>
      </Panel>

      {data.kind === "ready" && data.truncated && (
        <p className="pnl-chart-note">
          Charts show the latest {MAX_PAGES * PAGE_SIZE} settled runs in this period. Totals above still cover every run.
        </p>
      )}
    </div>
  );
}

function ChartBody({ data, empty, onRetry, children }: {
  data: ChartData;
  empty: boolean;
  onRetry: () => void;
  children: ReactNode;
}) {
  if (data.kind === "loading") {
    return <div className="skeleton pnl-chart-skeleton" role="status" aria-label="Loading chart" />;
  }
  if (data.kind === "error") {
    return (
      <div className="report-error">
        <InlineMessage tone="error">{data.message}</InlineMessage>
        <button type="button" className="button secondary small" onClick={onRetry}>
          <RefreshCw aria-hidden="true" />Try again
        </button>
      </div>
    );
  }
  if (empty) {
    return (
      <EmptyState
        compact
        icon={<BarChart3 />}
        title="Nothing settled in this period"
        description="Charts fill in once a run closes with final fills and fees."
      />
    );
  }
  return <>{children}</>;
}

/** Tooltip anchored to an x position, kept inside the chart's edges. */
function ChartTooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x, 96), Math.max(width - 96, 96));
  return <div className="pnl-tooltip" style={{ left }} aria-hidden="true">{children}</div>;
}

export function LineChart({ points }: { points: PnlPoint[] }) {
  const { ref, width } = useMeasuredWidth();
  const { convertFromUsd, formatMoney } = useCurrency();
  const axis = useAxisFormat();
  const [active, setActive] = useState<number | null>(null);
  const above = useSvgId("pnl-above");
  const below = useSvgId("pnl-below");
  const gainFill = useSvgId("pnl-gain-fill");
  const lossFill = useSvgId("pnl-loss-fill");

  /* The curve starts at zero before the first run, so the first result reads as a step up or down. */
  const values = useMemo(() => [0, ...points.map(point => convertFromUsd(point.cumulative))], [points, convertFromUsd]);
  const ticks = niceTicks(Math.min(...values), Math.max(...values));
  const plotWidth = Math.max(width - MARGIN.left - MARGIN.right, 1);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const y = yScale(ticks, plotHeight);
  const x = (index: number) => MARGIN.left + (values.length === 1 ? plotWidth / 2 : (index / (values.length - 1)) * plotWidth);
  const zero = y(0);

  const line = values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`).join("");
  const area = `${line}L${x(values.length - 1).toFixed(1)},${zero.toFixed(1)}L${x(0).toFixed(1)},${zero.toFixed(1)}Z`;
  const last = values.length - 1;
  const labelIndexes = [...new Set([1, Math.round((1 + last) / 2), last])];
  const point = active !== null ? points[active - 1] : null;

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left - MARGIN.left) / plotWidth;
    setActive(Math.min(last, Math.max(1, Math.round(ratio * last))));
  }

  function onKeyDown(event: ReactKeyboardEvent<SVGSVGElement>) {
    const next = stepIndex(event, active, 1, last);
    if (next === null) return;
    event.preventDefault();
    setActive(next);
  }

  const final = points[points.length - 1];
  const summary = `Cumulative P&L across ${points.length} settled runs from ${shortDate(points[0].at)} to ${shortDate(final.at)}, ending at ${formatMoney(final.cumulative, { signed: true })}. Use the arrow keys to read each run.`;

  return (
    <div className="pnl-chart" ref={ref}>
      {width > 0 && (
        <svg
          className="pnl-chart-svg"
          width={width}
          height={HEIGHT}
          viewBox={`0 0 ${width} ${HEIGHT}`}
          role="img"
          aria-label={summary}
          tabIndex={0}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive(current => current ?? last)}
          onBlur={() => setActive(null)}
          onKeyDown={onKeyDown}
        >
          <defs>
            <clipPath id={above}><rect x={0} y={0} width={width} height={zero} /></clipPath>
            <clipPath id={below}><rect x={0} y={zero} width={width} height={HEIGHT - zero} /></clipPath>
            {/* Gains fade downward to the zero line, losses fade upward to it. */}
            <linearGradient id={gainFill} gradientUnits="userSpaceOnUse" x1="0" y1={MARGIN.top} x2="0" y2={zero}>
              <stop offset="0%" className="pnl-stop is-gain" stopOpacity="0.32" />
              <stop offset="100%" className="pnl-stop is-gain" stopOpacity="0.03" />
            </linearGradient>
            <linearGradient id={lossFill} gradientUnits="userSpaceOnUse" x1="0" y1={HEIGHT - MARGIN.bottom} x2="0" y2={zero}>
              <stop offset="0%" className="pnl-stop is-loss" stopOpacity="0.32" />
              <stop offset="100%" className="pnl-stop is-loss" stopOpacity="0.03" />
            </linearGradient>
          </defs>

          {ticks.map(tick => (
            <g key={tick} className={tick === 0 ? "pnl-grid is-zero" : "pnl-grid"}>
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(tick)} y2={y(tick)} />
              <text x={MARGIN.left - 10} y={y(tick)} dy="0.32em" textAnchor="end">{axis(tick)}</text>
            </g>
          ))}

          <g className="pnl-series is-gain" clipPath={`url(#${above})`}>
            <path className="pnl-area" d={area} fill={`url(#${gainFill})`} />
            <path className="pnl-line" d={line} />
          </g>
          <g className="pnl-series is-loss" clipPath={`url(#${below})`}>
            <path className="pnl-area" d={area} fill={`url(#${lossFill})`} />
            <path className="pnl-line" d={line} />
          </g>

          {labelIndexes.map(index => (
            <text
              key={index}
              className="pnl-axis-x"
              x={x(index)}
              y={HEIGHT - 8}
              textAnchor={index === last && index !== 1 ? "end" : index === 1 && last > 1 ? "start" : "middle"}
            >
              {shortDate(points[index - 1].at)}
            </text>
          ))}

          {active !== null && (
            <g className="pnl-cursor">
              <line x1={x(active)} x2={x(active)} y1={MARGIN.top} y2={HEIGHT - MARGIN.bottom} />
              <circle
                className={values[active] >= 0 ? "is-gain" : "is-loss"}
                cx={x(active)}
                cy={y(values[active])}
                r={4.5}
              />
            </g>
          )}
        </svg>
      )}

      {point && active !== null && (
        <ChartTooltip x={x(active)} width={width}>
          <strong>{point.name}</strong>
          <small>{new Date(point.at).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</small>
          <span className="pnl-tooltip-row">
            <span>This run</span>
            <b className={point.pnl >= 0 ? "is-gain" : "is-loss"}>{formatMoney(point.pnl, { signed: true })}</b>
          </span>
          <span className="pnl-tooltip-row">
            <span>Running total</span>
            <b className={point.cumulative >= 0 ? "is-gain" : "is-loss"}>{formatMoney(point.cumulative, { signed: true })}</b>
          </span>
        </ChartTooltip>
      )}

      <p className="visually-hidden" aria-live="polite">
        {point
          ? `${point.name}, ${shortDate(point.at)}: ${formatMoney(point.pnl, { signed: true })}, running total ${formatMoney(point.cumulative, { signed: true })}`
          : ""}
      </p>
    </div>
  );
}

export function BarChart({ buckets, size }: { buckets: PnlBucket[]; size: BucketSize }) {
  const { ref, width } = useMeasuredWidth();
  const { convertFromUsd, formatMoney } = useCurrency();
  const axis = useAxisFormat();
  const [active, setActive] = useState<number | null>(null);

  const gains = buckets.map(bucket => convertFromUsd(bucket.gains));
  const losses = buckets.map(bucket => convertFromUsd(bucket.losses));
  const ticks = niceTicks(Math.min(0, ...losses), Math.max(0, ...gains));
  const plotWidth = Math.max(width - MARGIN.left - MARGIN.right, 1);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const y = yScale(ticks, plotHeight);
  const zero = y(0);
  const band = plotWidth / buckets.length;
  const barWidth = Math.min(Math.max(band * 0.62, 3), 40);
  const center = (index: number) => MARGIN.left + band * index + band / 2;
  const labelEvery = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor(plotWidth / 72))));
  const last = buckets.length - 1;
  const bucket = active !== null ? buckets[active] : null;

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const index = Math.floor((event.clientX - box.left - MARGIN.left) / band);
    setActive(index >= 0 && index <= last ? index : null);
  }

  function onKeyDown(event: ReactKeyboardEvent<SVGSVGElement>) {
    const next = stepIndex(event, active, 0, last);
    if (next === null) return;
    event.preventDefault();
    setActive(next);
  }

  const totalGains = buckets.reduce((sum, item) => sum + item.gains, 0);
  const totalLosses = buckets.reduce((sum, item) => sum + item.losses, 0);
  const summary = `Winnings and losses per ${size} across ${buckets.length} ${buckets.length === 1 ? size : `${size}s`}: ${formatMoney(totalGains, { signed: true })} won, ${formatMoney(totalLosses, { signed: true })} lost. Use the arrow keys to read each ${size}.`;

  return (
    <div className="pnl-chart" ref={ref}>
      {width > 0 && (
        <svg
          className="pnl-chart-svg"
          width={width}
          height={HEIGHT}
          viewBox={`0 0 ${width} ${HEIGHT}`}
          role="img"
          aria-label={summary}
          tabIndex={0}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive(current => current ?? last)}
          onBlur={() => setActive(null)}
          onKeyDown={onKeyDown}
        >
          {ticks.map(tick => (
            <g key={tick} className={tick === 0 ? "pnl-grid is-zero" : "pnl-grid"}>
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(tick)} y2={y(tick)} />
              <text x={MARGIN.left - 10} y={y(tick)} dy="0.32em" textAnchor="end">{axis(tick)}</text>
            </g>
          ))}

          {active !== null && (
            <rect className="pnl-band" x={MARGIN.left + band * active} y={MARGIN.top} width={band} height={plotHeight} />
          )}

          {buckets.map((item, index) => {
            const left = center(index) - barWidth / 2;
            const gainTop = y(gains[index]);
            const lossBottom = y(losses[index]);
            return (
              <g key={item.start}>
                {gains[index] > 0 && (
                  <rect className="pnl-bar is-gain" x={left} y={gainTop} width={barWidth} height={Math.max(zero - gainTop, 1)} rx={2} />
                )}
                {losses[index] < 0 && (
                  <rect className="pnl-bar is-loss" x={left} y={zero} width={barWidth} height={Math.max(lossBottom - zero, 1)} rx={2} />
                )}
                {index % labelEvery === 0 && (
                  <text className="pnl-axis-x" x={center(index)} y={HEIGHT - 8} textAnchor="middle">
                    {bucketLabel(item.start, size)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}

      {bucket && active !== null && (
        <ChartTooltip x={center(active)} width={width}>
          <strong>{bucketLabel(bucket.start, size, true)}</strong>
          <small>{bucket.runs} settled {bucket.runs === 1 ? "run" : "runs"} · {bucket.wins} won · {bucket.lost} lost</small>
          <span className="pnl-tooltip-row"><span>Winnings</span><b className="is-gain">{formatMoney(bucket.gains, { signed: true })}</b></span>
          <span className="pnl-tooltip-row"><span>Losses</span><b className="is-loss">{formatMoney(bucket.losses, { signed: true })}</b></span>
          <span className="pnl-tooltip-row">
            <span>Net</span>
            <b className={bucket.net >= 0 ? "is-gain" : "is-loss"}>{formatMoney(bucket.net, { signed: true })}</b>
          </span>
        </ChartTooltip>
      )}

      <p className="visually-hidden" aria-live="polite">
        {bucket
          ? `${bucketLabel(bucket.start, size, true)}: won ${formatMoney(bucket.gains)}, lost ${formatMoney(Math.abs(bucket.losses))}, net ${formatMoney(bucket.net, { signed: true })}, ${bucket.runs} runs`
          : ""}
      </p>
    </div>
  );
}
