"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Activity, AlertTriangle, RefreshCw } from "@/app/components/icons";
import { useCurrency } from "@/app/components/currency";
import { requestJson } from "@/lib/api";
import { EM_DASH, errorMessage, formatDateTime, relativeTime, toNumber } from "@/lib/format";
import { RANGE_OPTIONS, STATE_LABELS, STATE_TONES, exclusionText, winRateText } from "@/lib/reporting";
import type { AccountingState, PnlSummary, ReportRange, TradeItem, TradePage } from "@/lib/app-types";
import {
  EmptyState, IconSwap, InlineMessage, Segmented, Select, StatusChip, TableSkeleton
} from "@/app/components/ui";

export type StateFilter = AccountingState | "all";

const STATE_OPTIONS: { value: StateFilter; label: string }[] = [
  { value: "all", label: "All states" },
  ...(Object.keys(STATE_LABELS) as AccountingState[]).map(state => ({ value: state, label: STATE_LABELS[state] }))
];

function pnlTone(value: number | null) {
  if (value === null || value === 0) return "neutral";
  return value > 0 ? "positive" : "negative";
}

/**
 * Headline figures over settled runs only, plus what was left out and why.
 * `scopeNote` says whose view this is, because personal and owner totals differ by design.
 */
export function PnlTiles({ summary, scopeNote }: { summary: PnlSummary; scopeNote: ReactNode }) {
  const { currencyCode, formatMoneyNumber } = useCurrency();
  const net = toNumber(summary.netRealizedPnl);
  const unresolved = summary.states.open + summary.states.incomplete + summary.states.attention;
  return (
    <div className="pnl-summary">
      <div className="detail-tiles pnl-tiles">
        <Tile label="Net realized P&L" value={formatMoneyNumber(summary.netRealizedPnl, { digits: 2, signed: true })}
          suffix={currencyCode} tone={pnlTone(net)} emphasis />
        <Tile label="Winning runs total" value={formatMoneyNumber(summary.grossGains, { digits: 2, signed: true })}
          suffix={currencyCode} tone={pnlTone(toNumber(summary.grossGains))} />
        <Tile label="Losing runs total" value={formatMoneyNumber(summary.grossLosses, { digits: 2, signed: true })}
          suffix={currencyCode} tone={pnlTone(toNumber(summary.grossLosses))} />
        <Tile label="Exchange fees" value={formatMoneyNumber(summary.exchangeFees, { digits: 2 })} suffix={currencyCode} />
        <Tile label="Win rate" value={winRateText(summary.winRate)}
          detail={`${summary.wins} won · ${summary.losses} lost · ${summary.breakEven} even`} />
        <Tile label="Settled runs" value={String(summary.settledRuns)} detail={`of ${summary.totalRuns} in this period`} />
      </div>
      <p className="pnl-scope-note">
        {scopeNote}{" "}
        {summary.excludedRuns > 0
          ? `${summary.excludedRuns} ${summary.excludedRuns === 1 ? "run is" : "runs are"} not counted: ${summary.states.scheduled} scheduled, ${summary.states.open} open, ${summary.states.cancelled} cancelled, ${summary.states.incomplete} incomplete, ${summary.states.attention} need attention.`
          : "Every run in this period is counted."}
        {unresolved > 0 && " Open positions and runs with incomplete fills or fees are left out until they settle."}
      </p>
    </div>
  );
}

function Tile({ label, value, suffix, detail, tone = "neutral", emphasis = false }: {
  label: string;
  value: string;
  suffix?: string;
  detail?: string;
  tone?: "neutral" | "positive" | "negative";
  emphasis?: boolean;
}) {
  return (
    <div className={`detail-tile tone-${tone}${emphasis ? " is-emphasis" : ""}`}>
      <span>{label}</span>
      <strong>{value}{suffix && <small>{suffix}</small>}</strong>
      {detail && <small className="detail-tile-note">{detail}</small>}
    </div>
  );
}

export function RangeControl({ value, onChange }: { value: ReportRange; onChange: (value: ReportRange) => void }) {
  return (
    <div className="report-range">
      <Segmented
        label="Period"
        value={value}
        options={RANGE_OPTIONS.map(option => ({ value: option.value, label: option.label }))}
        onChange={next => {
          const match = RANGE_OPTIONS.find(option => option.value === next);
          if (match) onChange(match.value);
        }}
      />
    </div>
  );
}

export function StateSelect({ value, onChange }: { value: StateFilter; onChange: (value: StateFilter) => void }) {
  return (
    <Select
      label="Accounting state"
      value={value}
      options={STATE_OPTIONS}
      onChange={next => {
        const match = STATE_OPTIONS.find(option => option.value === next);
        if (match) onChange(match.value);
      }}
    />
  );
}

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; items: TradeItem[]; nextCursor: string | null; more: "idle" | "loading" | "error" };

/**
 * Cursor-paged trade list. `path` must already include every filter; changing it
 * restarts from the first page. Older requests are ignored once a newer one starts.
 */
export function useTradePages(path: string | null) {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const generation = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const current = ++generation.current;
    setState(previous => previous.kind === "ready" ? previous : { kind: "loading" });
    try {
      const page = await requestJson<TradePage>(path);
      if (current === generation.current) {
        setState({ kind: "ready", items: page.items, nextCursor: page.nextCursor, more: "idle" });
      }
    } catch (error) {
      if (current === generation.current) setState({ kind: "error", message: errorMessage(error) });
    }
  }, [path]);

  useEffect(() => {
    setState({ kind: "loading" });
    void load();
  }, [load]);

  const loadMore = useCallback(async () => {
    if (!path || state.kind !== "ready" || !state.nextCursor || state.more === "loading") return;
    const current = generation.current;
    const cursor = state.nextCursor;
    setState({ ...state, more: "loading" });
    try {
      const page = await requestJson<TradePage>(`${path}&cursor=${encodeURIComponent(cursor)}`);
      if (current !== generation.current) return;
      setState(previous => previous.kind === "ready"
        ? { kind: "ready", items: [...previous.items, ...page.items], nextCursor: page.nextCursor, more: "idle" }
        : previous);
    } catch {
      if (current === generation.current) setState(previous => previous.kind === "ready" ? { ...previous, more: "error" } : previous);
    }
  }, [path, state]);

  return { state, reload: load, loadMore };
}

/** Software trades with their accounting state. Deleted runs are marked when `showDeleted` is set. */
export function TradeTable({ state, onInspect, onRetry, onLoadMore, showDeleted = false, emptyText }: {
  state: ListState;
  onInspect: (trade: TradeItem) => void;
  onRetry: () => void;
  onLoadMore: () => void;
  showDeleted?: boolean;
  emptyText: string;
}) {
  const { currencyCode, formatMoneyNumber } = useCurrency();
  if (state.kind === "loading") return <TableSkeleton label="software trades" rows={5} />;
  if (state.kind === "error") {
    return (
      <div className="report-error">
        <InlineMessage tone="error">{state.message}</InlineMessage>
        <button type="button" className="button secondary small" onClick={onRetry}>
          <RefreshCw aria-hidden="true" />Try again
        </button>
      </div>
    );
  }
  if (!state.items.length) {
    return <EmptyState compact icon={<Activity />} title="No software trades here" description={emptyText} />;
  }
  return (
    <>
      <div className="table-scroll mobile-card-list t-reveal">
        <table className="data-table trade-table mobile-card-table">
          <caption className="visually-hidden">Software trades with accounting state and realized P&L</caption>
          <thead>
            <tr>
              <th scope="col">Strategy</th>
              <th scope="col">Date</th>
              <th scope="col">State</th>
              <th scope="col">Realized P&L ({currencyCode})</th>
              <th scope="col">Fees ({currencyCode})</th>
              <th scope="col">Budget at entry ({currencyCode})</th>
            </tr>
          </thead>
          <tbody>
            {state.items.map(trade => {
              const settled = trade.accountingState === "settled";
              const realized = toNumber(trade.realizedPnl);
              return (
                <tr key={trade.runId} className={trade.deletedByUserAt ? "trade-row is-deleted" : "trade-row"}>
                  <th scope="row">
                    <button type="button" className="run-name" onClick={() => onInspect(trade)}>
                      <span className="cell-stack">
                        <strong>{trade.name}</strong>
                        <small>
                          {trade.asset ?? "BTC"}
                          {showDeleted && trade.deletedByUserAt && (
                            <> · <span className="deleted-mark">Deleted by user {relativeTime(trade.deletedByUserAt)}</span></>
                          )}
                        </small>
                      </span>
                    </button>
                  </th>
                  <td data-label="Date">
                    <span className="cell-stack">
                      <span>{formatDateTime(trade.activityAt)}</span>
                      <small>{relativeTime(trade.activityAt)}</small>
                    </span>
                  </td>
                  <td data-label="State">
                    <StatusChip tone={STATE_TONES[trade.accountingState]}>{STATE_LABELS[trade.accountingState]}</StatusChip>
                  </td>
                  <td data-label="Realized P&L" className={settled && realized ? (realized > 0 ? "up" : "down") : undefined}>
                    {settled ? formatMoneyNumber(trade.realizedPnl, { digits: 4, signed: true }) : (
                      <span className="cell-stack">
                        <span>Not counted</span>
                        {exclusionText(trade.exclusionReason) && <small>{exclusionText(trade.exclusionReason)}</small>}
                      </span>
                    )}
                  </td>
                  <td data-label="Fees">{trade.exchangeFees ? formatMoneyNumber(trade.exchangeFees, { digits: 4 }) : EM_DASH}</td>
                  <td data-label="Budget at entry">
                    {trade.capitalBudget ? formatMoneyNumber(trade.capitalBudget) : EM_DASH}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {(state.nextCursor || state.more === "error") && (
        <div className="report-more">
          {state.more === "error" && (
            <span className="report-more-error"><AlertTriangle aria-hidden="true" />The next page did not load.</span>
          )}
          <button type="button" className="button secondary small" onClick={onLoadMore} disabled={state.more === "loading"}>
            <IconSwap showB={state.more === "loading"} a={<RefreshCw />} b={<RefreshCw className="spin" />} />
            {state.more === "error" ? "Retry" : "Load more"}
          </button>
        </div>
      )}
    </>
  );
}
