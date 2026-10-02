"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, RefreshCw } from "@/app/components/icons";
import { requestJson } from "@/lib/api";
import { errorMessage, formatTimestamp, relativeTime } from "@/lib/format";
import { REPORT_ASSET_OPTIONS, pnlQuery, runStub, tradeQuery, type ReportAsset } from "@/lib/reporting";
import type { PnlResponse, ReportRange, TradeItem } from "@/lib/app-types";
import { RunDetailDialog } from "@/app/components/RunHistory";
import PnlCharts from "@/app/components/PnlCharts";
import {
  PnlTiles, RangeControl, StateSelect, TradeTable, useTradePages, type StateFilter
} from "@/app/components/TradeReport";
import {
  IconSwap, InlineMessage, Panel, PanelHeader, SectionHeading, Segmented, TileSkeleton
} from "@/app/components/ui";

type SummaryState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: PnlResponse; refresh: "idle" | "loading" | "failed" };

/**
 * Personal P&L.
 *
 * Only runs this software placed for the signed-in account, and never runs the
 * account holder deleted from their history. Manual Delta trades are not included.
 */
export default function PnlView() {
  const [range, setRange] = useState<ReportRange>("30d");
  const [asset, setAsset] = useState<ReportAsset>("all");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [summary, setSummary] = useState<SummaryState>({ kind: "loading" });
  const [inspecting, setInspecting] = useState<TradeItem | null>(null);
  const [chartsToken, setChartsToken] = useState(0);
  const generation = useRef(0);
  const trades = useTradePages(`/api/me/trades?${tradeQuery({ range, asset, state: stateFilter })}`);

  const loadSummary = useCallback(async (refresh = false) => {
    const current = ++generation.current;
    setSummary(previous => refresh && previous.kind === "ready" ? { ...previous, refresh: "loading" } : { kind: "loading" });
    try {
      const data = await requestJson<PnlResponse>(`/api/me/pnl?${pnlQuery({ range, asset })}`);
      if (current === generation.current) setSummary({ kind: "ready", data, refresh: "idle" });
    } catch (error) {
      if (current !== generation.current) return;
      // A failed refresh keeps the last figures on screen and says they are stale.
      setSummary(previous => previous.kind === "ready"
        ? { ...previous, refresh: "failed" }
        : { kind: "error", message: errorMessage(error) });
    }
  }, [range, asset]);

  useEffect(() => { void loadSummary(); }, [loadSummary]);

  const refreshing = summary.kind === "ready" && summary.refresh === "loading";

  function refresh() {
    void loadSummary(true);
    void trades.reload();
    setChartsToken(token => token + 1);
  }

  return (
    <div className="report-page">
      <SectionHeading
        title="My P&L"
        description="Results of strategy runs this software placed on your Delta account. Manual Delta trades are not included."
        actions={
          <button type="button" className="button secondary small" onClick={refresh} disabled={refreshing}>
            <IconSwap showB={refreshing} a={<RefreshCw />} b={<RefreshCw className="spin" />} />
            Refresh
          </button>
        }
      />

      <div className="report-filters">
        <RangeControl value={range} onChange={setRange} />
        <Segmented
          label="Asset"
          value={asset}
          options={REPORT_ASSET_OPTIONS.map(option => ({ ...option }))}
          onChange={next => {
            const match = REPORT_ASSET_OPTIONS.find(option => option.value === next);
            if (match) setAsset(match.value);
          }}
        />
      </div>

      {summary.kind === "ready" && !summary.data.historyComplete && (
        <p className="callout tone-warning" role="status">
          <AlertTriangle aria-hidden="true" />
          <span>
            <strong>Earlier runs are still being imported.</strong>
            {" "}Totals cover the runs recorded so far and may grow once the import is verified.
          </span>
        </p>
      )}

      <Panel className="report-panel">
        <PanelHeader
          title="Performance"
          meta={summary.kind === "ready"
            ? summary.refresh === "failed"
              ? `Could not refresh. Showing figures from ${formatTimestamp(summary.data.asOf)}.`
              : `As of ${relativeTime(summary.data.asOf)}`
            : undefined}
        />
        {summary.kind === "loading" ? (
          <TileSkeleton count={6} />
        ) : summary.kind === "error" ? (
          <div className="report-error">
            <InlineMessage tone="error">{summary.message}</InlineMessage>
            <button type="button" className="button secondary small" onClick={() => void loadSummary()}>
              <RefreshCw aria-hidden="true" />Try again
            </button>
          </div>
        ) : (
          <PnlTiles
            summary={summary.data.summary}
            scopeNote="Only fully closed runs with final fills and fees count. Runs you deleted from your history are not included."
          />
        )}
      </Panel>

      <PnlCharts key={`${range}:${asset}`} range={range} asset={asset} refreshToken={chartsToken} />

      <Panel className="report-panel">
        <PanelHeader title="Software trades" meta="Newest first. Open a trade to see its fills and settlement." />
        <div className="report-filters">
          <StateSelect value={stateFilter} onChange={setStateFilter} />
        </div>
        <TradeTable
          state={trades.state}
          onInspect={setInspecting}
          onRetry={() => void trades.reload()}
          onLoadMore={() => void trades.loadMore()}
          emptyText={asset === "all"
            ? "Runs this software places for you will appear here with their results."
            : `No ${asset} runs match this period and accounting state.`}
        />
      </Panel>

      {inspecting && (
        <RunDetailDialog run={runStub(inspecting)} refreshToken={0} onClose={() => setInspecting(null)} />
      )}
    </div>
  );
}
