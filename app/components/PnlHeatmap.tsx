"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { RefreshCw } from "@/app/components/icons";
import { useCurrency } from "@/app/components/currency";
import { InlineMessage, Panel, PanelHeader, Select } from "@/app/components/ui";
import { requestJson } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { pnlQuery, verifyReportAsset, verifyReportStrategy, type ReportAsset } from "@/lib/reporting";
import { calendarDateLabel, calendarDates, calendarYears, dayTone, parsePnlCalendar } from "@/lib/pnl-calendar";
import type { PnlCalendarResponse, PnlDay, ReportRange } from "@/lib/app-types";

type CalendarState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: PnlCalendarResponse; refreshing: boolean; refreshError: string | null };

export default function PnlHeatmap({ range, asset, strategy, refreshToken }: {
  range: ReportRange; asset: ReportAsset; strategy: string | null; refreshToken: number;
}) {
  const [state, setState] = useState<CalendarState>({ kind: "loading" });
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const generation = useRef(0);
  const load = useCallback(async () => {
    const current = ++generation.current;
    setState(previous => previous.kind === "ready"
      ? { ...previous, refreshing: true, refreshError: null } : { kind: "loading" });
    try {
      const data = parsePnlCalendar(await requestJson<unknown>(`/api/me/pnl/calendar?${pnlQuery({ range, asset, strategy })}`));
      verifyReportAsset(data, asset);
      verifyReportStrategy(data, strategy);
      if (data.range !== range) throw new Error("The calendar did not return the selected period.");
      if (current === generation.current) setState({ kind: "ready", data, refreshing: false, refreshError: null });
    } catch (error) {
      if (current !== generation.current) return;
      setState(previous => previous.kind === "ready"
        ? { ...previous, refreshing: false, refreshError: errorMessage(error) }
        : { kind: "error", message: errorMessage(error) });
    }
  }, [range, asset, strategy]);

  useEffect(() => {
    void load();
    return () => { generation.current += 1; };
  }, [load, refreshToken]);

  const data = state.kind === "ready" ? state.data : null;
  const years = data ? calendarYears(data.days, data.endDate) : [];
  const year = selectedYear !== null && years.includes(selectedYear) ? selectedYear : years[0];
  const start = data ? range === "all" ? `${year}-01-01` : data.startDate ?? data.endDate : "";
  const end = data ? range === "all" ? `${year}-12-31` : data.endDate : "";
  const visibleDays = useMemo(() => data?.days.filter(day => day.date >= start && day.date <= end) ?? [], [data, start, end]);
  const { formatMoney } = useCurrency();
  const net = visibleDays.reduce((sum, day) => sum + Number(day.netRealizedPnl), 0);
  const settled = visibleDays.reduce((sum, day) => sum + day.settledRuns, 0);

  return <Panel className="report-panel pnl-calendar-panel">
    <PanelHeader title="Daily P&L" meta="Net settled results after fees · IST"
      actions={range === "all" && data ? <Select label="Calendar year" value={String(year)}
        options={years.map(value => ({ value: String(value), label: String(value) }))}
        onChange={value => setSelectedYear(Number(value))} /> : undefined} />
    {state.kind === "loading" ? <div className="skeleton pnl-calendar-skeleton" role="status" aria-label="Loading daily P&L" />
      : state.kind === "error" ? <div className="report-error">
        <InlineMessage tone="error">{state.message}</InlineMessage>
        <button type="button" className="button secondary small" onClick={() => void load()}><RefreshCw />Try again</button>
      </div> : <>
        <div className="pnl-calendar-summary" aria-busy={state.refreshing}>
          <strong className={net > 0 ? "up" : net < 0 ? "down" : ""}>{formatMoney(net, { signed: true })}</strong>
          <span>{settled} settled {settled === 1 ? "trade" : "trades"} · {visibleDays.length} trading {visibleDays.length === 1 ? "day" : "days"}
            {range === "all" ? ` in ${year}` : " in this period"}</span>
          {state.refreshing && <small role="status">Refreshing…</small>}
        </div>
        {state.refreshError && <InlineMessage tone="error">Calendar refresh failed. Showing the previous results. {state.refreshError}</InlineMessage>}
        {!state.data.historyComplete && <p className="pnl-chart-note">Earlier runs are still being imported. Some days may be missing.</p>}
        <CalendarGrid key={`${start}:${end}`} start={start} end={end} today={state.data.endDate} days={visibleDays} />
        {!settled && <p className="pnl-chart-note">No settled trades in this calendar period. Days fill in once trades close with final fills and fees.</p>}
      </>}
  </Panel>;
}

function DayDetails({ date, day }: { date: string; day?: PnlDay }) {
  const { formatMoney } = useCurrency();
  return <>
    <strong>{calendarDateLabel(date)} <small>IST</small></strong>
    {day ? <>
      <span className="pnl-tooltip-row"><span>Net P&L</span><b className={Number(day.netRealizedPnl) > 0 ? "up" : Number(day.netRealizedPnl) < 0 ? "down" : ""}>{formatMoney(day.netRealizedPnl, { signed: true })}</b></span>
      <span className="pnl-tooltip-row"><span>Winning total</span><b className="up">{formatMoney(day.grossGains, { signed: true })}</b></span>
      <span className="pnl-tooltip-row"><span>Losing total</span><b className="down">{formatMoney(day.grossLosses, { signed: true })}</b></span>
      <span className="pnl-tooltip-row"><span>Fees, already deducted</span><b>{formatMoney(day.exchangeFees)}</b></span>
      <small>{day.settledRuns} settled · {day.wins} won · {day.losses} lost · {day.breakEven} even</small>
    </> : <span>No settled trades</span>}
  </>;
}

function CalendarGrid({ start, end, today, days }: { start: string; end: string; today: string; days: PnlDay[] }) {
  const dates = useMemo(() => calendarDates(start, end), [start, end]);
  const byDate = useMemo(() => new Map(days.map(day => [day.date, day])), [days]);
  const maximum = Math.max(0, ...days.map(day => Math.abs(Number(day.netRealizedPnl))));
  const lastDate = end > today ? today : end;
  const [focused, setFocused] = useState(lastDate);
  const [selected, setSelected] = useState(days[days.length - 1]?.date ?? lastDate);
  const [tooltip, setTooltip] = useState<{ date: string; left: number; top: number; above: boolean } | null>(null);
  const grid = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const detailsId = useId();
  const { formatMoney } = useCurrency();
  const weeks = dates.length / 7;

  useEffect(() => {
    // The most recent dates stay visible on a phone; older weeks remain scrollable.
    const container = scroll.current;
    const cell = grid.current?.querySelector<HTMLButtonElement>(`[data-date="${lastDate}"]`);
    if (container && cell) {
      container.scrollLeft += cell.getBoundingClientRect().right - container.getBoundingClientRect().right + 16;
    }
  }, [lastDate]);

  useEffect(() => {
    const dismiss = () => setTooltip(null);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, []);

  function inspect(date: string, node: HTMLButtonElement) {
    const bounds = node.getBoundingClientRect();
    const width = Math.min(280, window.innerWidth - 24);
    setTooltip({ date, left: Math.max(12, Math.min(bounds.left + bounds.width / 2 - width / 2, window.innerWidth - width - 12)),
      top: bounds.top < 220 ? bounds.bottom + 10 : bounds.top - 10, above: bounds.top >= 220 });
    setSelected(date);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, date: string) {
    const index = dates.indexOf(date);
    const offset = event.key === "ArrowLeft" ? -7 : event.key === "ArrowRight" ? 7
      : event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : null;
    if (event.key === "Escape") { setTooltip(null); return; }
    if ((event.key === "ArrowUp" && index % 7 === 0) || (event.key === "ArrowDown" && index % 7 === 6)) {
      event.preventDefault();
      return;
    }
    let next: string | undefined;
    if (event.key === "Home") next = start;
    else if (event.key === "End") next = lastDate;
    else if (offset !== null) next = dates[Math.max(0, Math.min(dates.length - 1, index + offset))];
    else return;
    event.preventDefault();
    if (!next || next < start || next > lastDate) return;
    setFocused(next);
    grid.current?.querySelector<HTMLButtonElement>(`[data-date="${next}"]`)?.focus();
  }

  const monthLabels = Array.from({ length: weeks }, (_, week) => {
    const date = dates[week * 7] < start ? start : dates[week * 7];
    const previous = week ? (dates[(week - 1) * 7] < start ? start : dates[(week - 1) * 7]).slice(0, 7) : null;
    return date.slice(0, 7) !== previous ? new Intl.DateTimeFormat("en", { month: "short", timeZone: "UTC" })
      .format(new Date(`${date}T00:00:00Z`)) : "";
  });

  return <>
    <div className="pnl-calendar-scroll" ref={scroll} onScroll={() => setTooltip(null)}>
      <div className="pnl-calendar-months" style={{ gridTemplateColumns: `32px repeat(${weeks}, var(--calendar-cell))` }} aria-hidden="true">
        <span />{monthLabels.map((label, index) => <span key={dates[index * 7]}>{label}</span>)}
      </div>
      <div ref={grid} role="grid" aria-label="Daily settled P&L in IST. Arrow keys move between days and weeks."
        aria-rowcount={7} aria-colcount={weeks} className="pnl-calendar-grid" onPointerLeave={() => setTooltip(null)}>
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((weekday, row) => <div role="row" key={weekday}
          className="pnl-calendar-row" style={{ gridTemplateColumns: `32px repeat(${weeks}, var(--calendar-cell))` }}>
          <span className="pnl-calendar-weekday" aria-hidden="true">{row % 2 === 0 ? weekday : ""}</span>
          {Array.from({ length: weeks }, (_, week) => {
            const date = dates[week * 7 + row];
            const padding = date < start || date > end;
            const future = date > today;
            const day = byDate.get(date);
            const tone = dayTone(day, maximum);
            const label = `${calendarDateLabel(date)}, IST. ${future ? "Future date" : day
              ? `${formatMoney(day.netRealizedPnl, { signed: true })} net P&L, ${day.settledRuns} settled trades, ${day.wins} won, ${day.losses} lost, ${day.breakEven} even`
              : "No settled trades"}`;
            return <div role="gridcell" key={date} aria-colindex={week + 1} aria-selected={selected === date}>
              <button type="button" data-date={date} data-tone={tone}
                className={`pnl-calendar-cell${padding ? " is-padding" : ""}${future ? " is-future" : ""}${selected === date ? " is-selected" : ""}`}
                disabled={padding || future} tabIndex={date === focused && !padding && !future ? 0 : -1}
                aria-label={label} aria-describedby={date === selected ? detailsId : undefined}
                onPointerEnter={event => inspect(date, event.currentTarget)}
                onFocus={event => { setFocused(date); inspect(date, event.currentTarget); }}
                onBlur={() => setTooltip(null)} onKeyDown={event => onKeyDown(event, date)}
                onClick={event => { setFocused(date); inspect(date, event.currentTarget); }} />
            </div>;
          })}
        </div>)}
      </div>
    </div>
    <div className="pnl-calendar-footer">
      <span>Each square is one settlement day.</span>
      <div className="pnl-calendar-legend" aria-label="Color intensity shows daily net P&L magnitude">
        <span>Loss</span>{[4, 3, 2, 1].map(level => <i key={`loss-${level}`} data-tone={`loss-${level}`} />)}
        <i data-tone="empty" /><span>Profit</span>{[1, 2, 3, 4].map(level => <i key={`gain-${level}`} data-tone={`gain-${level}`} />)}
        <span className="pnl-calendar-legend-even"><i data-tone="even" />Break-even</span>
      </div>
    </div>
    <div className="pnl-calendar-readout" id={detailsId} aria-live="polite" aria-atomic="true">
      <DayDetails date={selected} day={byDate.get(selected)} />
    </div>
    {tooltip && createPortal(<div className="pnl-calendar-tooltip t-tt is-calendar-visible" role="tooltip" aria-hidden="true"
      style={{ left: tooltip.left, top: tooltip.top, transformOrigin: "center", transform: tooltip.above ? "translateY(-100%)" : "none" }}>
      <DayDetails date={tooltip.date} day={byDate.get(tooltip.date)} />
    </div>, document.body)}
  </>;
}
