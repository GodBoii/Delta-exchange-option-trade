"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { requestJson } from "@/lib/api";
import type { PnlResponse, ReportRange, TradeItem, TradePage } from "@/lib/app-types";
import { errorMessage } from "@/lib/format";
import { REPORT_ASSET_OPTIONS, STATE_LABELS, tradeQuery, type ReportAsset } from "@/lib/reporting";
import { bucketize, bucketSizeFor, type PnlPoint } from "@/lib/pnl-series";
import { ANALYTICS_ZONE, filterStrategyTrades, settlementHeatmaps, strategyCsv, strategyMetrics } from "@/lib/strategy-analytics";
import { BarChart3, RefreshCw } from "@/app/components/icons";
import { useCurrency } from "@/app/components/currency";
import { BarChart, LineChart } from "@/app/components/PnlCharts";
import { RangeControl } from "@/app/components/TradeReport";
import { EmptyState, InlineMessage, Panel, PanelHeader, SectionHeading, Segmented, TableSkeleton } from "@/app/components/ui";

type DataState = { kind: "loading" } | { kind: "error"; message: string }
  | { kind: "ready"; trades: TradeItem[]; nextCursor: string | null; at: string; historyComplete: boolean };
const BATCH_PAGES = 20;

export default function StrategyAnalytics({ userId }: { userId: string }) {
  const [range, setRange] = useState<ReportRange>("30d");
  const [asset, setAsset] = useState<ReportAsset>("all");
  const [strategy, setStrategy] = useState<string | null>(null);
  const [data, setData] = useState<DataState>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async (previous?: Extract<DataState, { kind: "ready" }>) => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const current = ++generation.current;
    setBusy(true);
    setRefreshError(null);
    if (!previous) setData({ kind: "loading" });
    try {
      const trades = [...(previous?.trades ?? [])];
      let cursor = previous?.nextCursor ?? null;
      let pages = 0;
      do {
        const query = tradeQuery({ range, state: "all", deleted: "include", limit: 50, cursor });
        const page = await requestJson<TradePage>(`/api/owner/users/${encodeURIComponent(userId)}/trades?${query}`,
          { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15_000)]) });
        if (!Array.isArray(page.items) || (page.nextCursor !== null && typeof page.nextCursor !== "string")) {
          throw new Error("The server returned an invalid trade page.");
        }
        trades.push(...page.items);
        cursor = page.nextCursor;
        pages += 1;
      } while (cursor && pages < BATCH_PAGES && !abort.signal.aborted);
      const history = await requestJson<PnlResponse>(`/api/me/pnl?range=${range}`,
        { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15_000)]) });
      if (current === generation.current) setData({ kind: "ready", trades: [...new Map(trades.map(trade => [trade.runId, trade])).values()],
        nextCursor: cursor, at: new Date().toISOString(), historyComplete: history.historyComplete });
    } catch (error) {
      if (abort.signal.aborted || current !== generation.current) return;
      if (previous) setRefreshError(errorMessage(error));
      else setData({ kind: "error", message: errorMessage(error) });
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }, [range, userId]);

  useEffect(() => {
    void load();
    return () => { generation.current += 1; controller.current?.abort(); };
  }, [load]);

  const marketTrades = useMemo(() => filterStrategyTrades(data.kind === "ready" ? data.trades : [], asset, null), [data, asset]);
  const names = useMemo(() => [...new Set(marketTrades.map(trade => trade.name))].sort((a, b) => a.localeCompare(b)), [marketTrades]);
  const selected = strategy !== null && names.includes(strategy) ? strategy : null;
  const trades = useMemo(() => filterStrategyTrades(marketTrades, asset, selected), [marketTrades, asset, selected]);
  const metrics = useMemo(() => strategyMetrics(trades), [trades]);
  const { formatMoney, currencyCode } = useCurrency();

  function exportCsv() {
    const url = URL.createObjectURL(new Blob([strategyCsv(trades)], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `strategy-pnl-${asset}-${range}.csv`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div className="report-page strategy-analytics">
    <SectionHeading title="Strategy analytics" description="Your BTC and ETH strategy results from the Ubuntu trade ledger. Owner access only."
      actions={<button type="button" className="button secondary small" disabled={busy} onClick={() => void load()}>
        <RefreshCw className={busy ? "spin" : undefined} aria-hidden="true" />Refresh
      </button>} />
    <div className="analytics-toolbar">
      <RangeControl value={range} onChange={next => { setRange(next); setStrategy(null); }} />
      <Segmented label="Asset" value={asset} options={REPORT_ASSET_OPTIONS.map(option => ({ ...option }))}
        onChange={next => { const match = REPORT_ASSET_OPTIONS.find(option => option.value === next); if (match) { setAsset(match.value); setStrategy(null); } }} />
      <label className="analytics-select">Strategy
        <select value={selected === null ? "" : String(names.indexOf(selected) + 1)} disabled={data.kind !== "ready"}
          onChange={event => setStrategy(names[Number(event.target.value) - 1] ?? null)}>
          <option value="">All strategies</option>
          {names.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}
        </select>
      </label>
      <button type="button" className="button secondary small" disabled={data.kind !== "ready" || !trades.length} onClick={exportCsv}>Export CSV</button>
    </div>
    {refreshError && <InlineMessage tone="error">{refreshError} Showing the previously loaded records.</InlineMessage>}
    {data.kind === "loading" ? <TableSkeleton label="owner strategy analytics" rows={8} /> : data.kind === "error"
      ? <InlineMessage tone="error">{data.message} Use Refresh to retry.</InlineMessage>
      : <>
        <p className="pnl-scope-note">{marketTrades.length} {asset === "all" ? "BTC / ETH" : asset} records loaded. Updated {new Date(data.at).toLocaleTimeString()}.
          {" "}Deleted history is retained in this owner view. Strategy names match the recorded run names exactly.
          {" "}Period filters use recorded activity time. Heatmaps use settlement time in IST.</p>
        {!data.historyComplete && <InlineMessage tone="warning">Earlier runs are still being imported. Results cover the ledger records available so far.</InlineMessage>}
        {data.nextCursor && <div className="analytics-partial" role="status">
          <p>Partial history. All figures and strategy choices below cover only the latest {data.trades.length} loaded records.</p>
          <button type="button" className="button secondary small" disabled={busy} onClick={() => void load(data)}>{busy ? "Loading older records…" : "Load older records"}</button>
        </div>}
        <Panel className="report-panel">
          <PanelHeader title={selected ?? "All strategy performance"} meta={`${asset === "all" ? "BTC + ETH" : asset} · ${currencyCode} display`} />
          <div className="analytics-metrics">
            <Metric label="Net realized P&L" value={formatMoney(metrics.net, { signed: true })} tone={metrics.net} note="Final recorded result after exchange fees" />
            <Metric label="Win rate" value={metrics.winRate === null ? "No settled runs" : `${(metrics.winRate * 100).toFixed(1)}%`} note={`${metrics.wins} won · ${metrics.lost} lost · ${metrics.even} even`} />
            <Metric label="Profit factor" value={metrics.profitFactor === null ? "N/A" : metrics.profitFactor.toFixed(2)} note="Winning net results / absolute losing net results" />
            <Metric label="Maximum drawdown" value={formatMoney(-metrics.maxDrawdown, { signed: true })} tone={-metrics.maxDrawdown} note="Largest fall from a cumulative P&L peak" />
            <Metric label="Average per settled run" value={metrics.average === null ? "N/A" : formatMoney(metrics.average, { signed: true })} tone={metrics.average ?? 0} />
            <Metric label="Recorded exchange fees" value={formatMoney(metrics.fees)} note={metrics.missingFees ? `${metrics.missingFees} settled fee values unavailable` : "Settled runs only"} />
          </div>
          <p className="pnl-scope-note">{metrics.settled} settled runs counted. {metrics.excluded} other records excluded from P&L. Open positions, pending fees and incomplete accounting do not count. Break-even runs count in win-rate totals. Profit factor is N/A when there are no losses. Drawdown starts from zero within the selected period.</p>
        </Panel>
        {!metrics.points.length ? <EmptyState compact icon={<BarChart3 />} title="No settled results for this selection" description="Change the strategy, asset or period. The ledger below still shows matching open and incomplete runs." />
          : <>
            <div className="pnl-charts">
              <Panel className="report-panel"><PanelHeader title="Cumulative P&L" meta="Settled results in settlement order" /><LineChart points={metrics.points} /></Panel>
              <Panel className="report-panel"><PanelHeader title="Winnings and losses" meta="Green: winning results · Red: losing results" /><BarChart buckets={bucketize(metrics.points, bucketSizeFor(metrics.points))} size={bucketSizeFor(metrics.points)} /></Panel>
              <Panel className="report-panel"><PanelHeader title="Drawdown" meta="Fall from the running P&L peak, in money" /><LineChart points={metrics.drawdown} metricLabel="Drawdown" /></Panel>
              <CalendarHeatmap points={metrics.points} />
            </div>
            <HourlyHeatmap points={metrics.points} />
          </>}
        <StrategyComparison trades={trades} />
        <AnalyticsLedger key={`${range}:${asset}:${selected}`} trades={trades} />
      </>}
  </div>;
}

function Metric({ label, value, note, tone = 0 }: { label: string; value: string; note?: string; tone?: number }) {
  return <div className="analytics-metric"><span>{label}</span><strong className={tone > 0 ? "analytics-gain" : tone < 0 ? "analytics-loss" : undefined}>{value}</strong>{note && <small>{note}</small>}</div>;
}

function HeatCell({ net, runs, label, max }: { net: number; runs: number; label: string; max: number }) {
  const { formatMoney } = useCurrency();
  const description = `${label}: ${runs ? `${formatMoney(net, { signed: true })}, ${runs} settled runs` : "no settled runs"}`;
  return <span className={`analytics-heat-cell ${runs ? net > 0 ? "is-gain" : net < 0 ? "is-loss" : "is-even" : "is-empty"}`}
    style={runs && net !== 0 ? { backgroundColor: `color-mix(in srgb, var(${net > 0 ? "--long" : "--short"}) ${18 + 38 * Math.abs(net) / Math.max(max, 1)}%, var(--surface-2))` } : undefined}
    tabIndex={0} aria-label={description} title={description}>{runs ? net > 0 ? "+" : net < 0 ? "−" : "0" : "·"}<span className="analytics-heat-tooltip" aria-hidden="true">{description}</span></span>;
}

function CalendarHeatmap({ points }: { points: PnlPoint[] }) {
  const { days } = useMemo(() => settlementHeatmaps(points), [points]);
  const months = [...new Set([...days.keys()].map(key => key.slice(0, 7)))].sort();
  const [chosen, setChosen] = useState<string | null>(null);
  const month = chosen && months.includes(chosen) ? chosen : months[months.length - 1];
  const start = new Date(`${month}-01T00:00:00Z`);
  const offset = (start.getUTCDay() + 6) % 7;
  const count = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
  const max = Math.max(1, ...[...days.entries()].filter(([key]) => key.startsWith(month)).map(([, day]) => Math.abs(day.net)));
  return <Panel className="report-panel"><PanelHeader title="Daily P&L heatmap" meta="Settlement date · IST" />
    <label className="analytics-select analytics-month">Month<select value={month} onChange={event => setChosen(event.target.value)}>{months.map(value => <option key={value}>{value}</option>)}</select></label>
    <div className="analytics-calendar">
      {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => <span className="analytics-day-label" key={day}>{day}</span>)}
      {Array.from({ length: offset }, (_, index) => <span key={`pad-${index}`} />)}
      {Array.from({ length: count }, (_, index) => {
        const date = `${month}-${String(index + 1).padStart(2, "0")}`;
        const day = days.get(date);
        return <div className="analytics-calendar-day" key={date}><small>{index + 1}</small><HeatCell label={date} net={day?.net ?? 0} runs={day?.runs ?? 0} max={max} /></div>;
      })}
    </div><HeatLegend />
  </Panel>;
}

function HeatLegend() {
  return <p className="pnl-scope-note">+ Profit · − Loss · 0 Break-even · Empty dots mean no settled runs. Stronger color means a larger absolute net result. Focus or hover a cell for amounts and run counts.</p>;
}

function HourlyHeatmap({ points }: { points: PnlPoint[] }) {
  const { hours } = useMemo(() => settlementHeatmaps(points), [points]);
  const max = Math.max(1, ...hours.flat().map(hour => Math.abs(hour.net)));
  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return <Panel className="report-panel"><PanelHeader title="Settlement timing" meta="Net P&L by weekday and settlement hour · IST · Each hour includes all loaded dates" />
    <div className="table-scroll"><div className="analytics-hour-grid"><span />
      {Array.from({ length: 24 }, (_, hour) => <span className="analytics-day-label" key={hour}>{String(hour).padStart(2, "0")}</span>)}
      {hours.map((row, day) => <div className="analytics-hour-row" key={day}><span className="analytics-day-label">{weekdays[day]}</span>{row.map((cell, hour) => <HeatCell key={hour} {...cell} max={max} label={`${weekdays[day]} ${String(hour).padStart(2, "0")}:00–${String(hour).padStart(2, "0")}:59 IST`} />)}</div>)}
    </div></div><HeatLegend />
  </Panel>;
}

function StrategyComparison({ trades }: { trades: TradeItem[] }) {
  const { formatMoney } = useCurrency();
  const rows = useMemo(() => {
    const groups = new Map<string, TradeItem[]>();
    for (const trade of trades) {
      const key = JSON.stringify([trade.name, trade.asset]);
      const group = groups.get(key) ?? [];
      group.push(trade);
      groups.set(key, group);
    }
    return [...groups.values()].map(group => ({ name: group[0].name, asset: group[0].asset, ...strategyMetrics(group) })).sort((a, b) => b.net - a.net);
  }, [trades]);
  return <Panel className="report-panel"><PanelHeader title="Strategy × asset breakdown" meta="Highest net P&L first. Same filters as the charts." />
    <div className="table-scroll"><table className="data-table analytics-table"><caption className="visually-hidden">Strategy performance by BTC and ETH</caption>
      <thead><tr>{["Strategy", "Asset", "Settled", "Excluded", "Net P&L", "Win rate", "Profit factor", "Max drawdown", "Fees"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={JSON.stringify([row.name, row.asset])}><th scope="row">{row.name}</th><td>{row.asset}</td><td>{row.settled}</td><td>{row.excluded}</td><td className={row.net >= 0 ? "analytics-gain" : "analytics-loss"}>{formatMoney(row.net, { signed: true })}</td><td>{row.winRate === null ? "N/A" : `${(row.winRate * 100).toFixed(1)}%`}</td><td>{row.profitFactor?.toFixed(2) ?? "N/A"}</td><td>{formatMoney(-row.maxDrawdown)}</td><td>{formatMoney(row.fees)}{row.missingFees > 0 && " *"}</td></tr>)}
        {!rows.length && <tr><td colSpan={9}>No matching strategy records.</td></tr>}</tbody>
    </table></div><p className="pnl-scope-note">* Some fee values are unavailable. Amounts use the selected display currency.</p>
  </Panel>;
}

function AnalyticsLedger({ trades }: { trades: TradeItem[] }) {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [state, setState] = useState("all");
  const { formatMoney } = useCurrency();
  const filtered = trades.filter(trade => (state === "all" || trade.accountingState === state) && trade.name.toLowerCase().includes(search.toLowerCase()));
  const current = Math.min(page, Math.max(0, Math.ceil(filtered.length / 25) - 1));
  const date = (trade: TradeItem) => new Date(trade.exitExecutedAt ?? trade.activityAt).toLocaleString("en-GB", { timeZone: ANALYTICS_ZONE, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  return <Panel className="report-panel"><PanelHeader title="Trade ledger" meta="Newest activity first · Settlement / activity date in IST · 25 rows per page" />
    <div className="analytics-toolbar"><label className="analytics-select">Search strategy<input type="search" value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} /></label>
      <label className="analytics-select">Accounting state<select value={state} onChange={event => { setState(event.target.value); setPage(0); }}><option value="all">All states</option>{Object.entries(STATE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
    <div className="table-scroll"><table className="data-table analytics-table"><caption className="visually-hidden">Filtered owner strategy trade ledger</caption>
      <thead><tr>{["Strategy / run", "Asset", "Settlement / activity IST", "State", "Net P&L", "Fees", "Allocated capital", "History"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
      <tbody>{filtered.slice(current * 25, (current + 1) * 25).map(trade => <tr key={trade.runId}><th scope="row">{trade.name}<small className="analytics-run-id" title={trade.runId}>{trade.runId.slice(0, 8)}</small></th><td>{trade.asset}</td><td>{date(trade)}</td><td>{STATE_LABELS[trade.accountingState]}</td><td>{trade.accountingState === "settled" ? formatMoney(trade.realizedPnl, { signed: true }) : "Not counted"}</td><td>{formatMoney(trade.exchangeFees)}</td><td>{formatMoney(trade.capitalBudget)}</td><td>{trade.deletedByUserAt ? "Deleted by user" : "Retained"}</td></tr>)}
        {!filtered.length && <tr><td colSpan={8}>No trades match these filters.</td></tr>}</tbody>
    </table></div><div className="analytics-pagination"><span>{filtered.length ? `${current * 25 + 1}–${Math.min((current + 1) * 25, filtered.length)} of ${filtered.length}` : "0 records"}</span><button type="button" className="button secondary small" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button><button type="button" className="button secondary small" disabled={(current + 1) * 25 >= filtered.length} onClick={() => setPage(current + 1)}>Next</button></div>
  </Panel>;
}
