"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertTriangle, Ban, CircleStop, Copy, Info, RefreshCw, Trash2
} from "lucide-react";
import { requestJson } from "@/lib/api";
import {
  EM_DASH, decimal, errorMessage, formatDateTime, formatDuration, formatTimestamp, relativeTime,
  signedDecimal, titleCase, toNumber
} from "@/lib/format";
import type { RunDetail, RunOrder, StrategyRun } from "@/lib/app-types";
import {
  AnimatedNumber, ConfirmModal, DetailList, DetailSection, Dialog, EmptyState, IconSwap,
  InlineMessage, Panel, RowMenu, SectionHeading, StatusChip, StatusDot, TableSkeleton,
  useSlidingPill, type DetailItem, type NoticeHandler, type RowMenuItem, type StatusTone
} from "@/app/components/ui";

const AUTO_REFRESH_MS = 30_000;

type FilterId = "all" | "pending" | "live" | "attention" | "closed";
type ActionKind = "cancel" | "exit" | "delete";

const FILTERS: { id: FilterId; label: string; match: (status: string) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "pending", label: "Scheduled", match: status => ["draft", "scheduled"].includes(status) },
  { id: "live", label: "Live", match: status => ["active", "executing_entry", "executing_exit"].includes(status) },
  { id: "attention", label: "Attention", match: status => status === "attention" },
  { id: "closed", label: "Closed", match: status => ["completed", "cancelled", "expired"].includes(status) }
];

const ACTION_COPY: Record<ActionKind, {
  title: string;
  confirm: string;
  describe: (name: string) => string;
}> = {
  cancel: {
    title: "Cancel scheduled strategy?",
    confirm: "Cancel strategy",
    describe: name => `${name} will be cancelled before entry and no orders will be placed. The run stays in history.`
  },
  exit: {
    title: "Exit live strategy?",
    confirm: "Exit strategy",
    describe: name => `${name} will cancel still-open entry orders, submit reduce-only market closes for recorded fills, and verify the live Delta positions.`
  },
  delete: {
    title: "Delete this run from history?",
    confirm: "Delete run",
    describe: name => `${name} and its execution record — orders, fills, slippage and settlement — will be permanently removed from Supabase. Delta positions are not affected. This cannot be undone.`
  }
};

function statusTone(status: string): StatusTone {
  if (status === "attention") return "negative";
  if (status === "completed") return "positive";
  if (status === "active" || status.startsWith("executing")) return "active";
  if (status === "scheduled" || status === "draft") return "warning";
  return "neutral";
}

/** Mirrors the backend rule: a run that may still hold a position cannot be erased. */
function canDeleteRun(run: StrategyRun) {
  if (["draft", "scheduled", "cancelled", "completed"].includes(run.status)) return true;
  return run.status === "attention" && (!run.entryExecutedAt || Boolean(run.exitExecutedAt));
}

/**
 * Operational log of scheduled runs.
 *
 * Every row exposes the same overflow menu, so the available reversals read the
 * same way whether a run is waiting, live, or settled: read the record, exit it,
 * cancel it before entry, or erase it once it is finished.
 */
export default function RunHistory({ onNotice, onAttentionChange }: {
  onNotice: NoticeHandler;
  /**
   * Reports the count of runs needing attention up to the shell, so the
   * navigation badge is correct the moment this view loads or an action lands,
   * rather than waiting for the shell's own slower background count.
   */
  onAttentionChange?: (count: number) => void;
}) {
  const [runs, setRuns] = useState<StrategyRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterId>("all");
  const [detail, setDetail] = useState<StrategyRun | null>(null);
  const [detailToken, setDetailToken] = useState(0);
  const [action, setAction] = useState<{ run: StrategyRun; kind: ActionKind } | null>(null);
  const [busy, setBusy] = useState(false);
  const report = useRef(onAttentionChange);
  report.current = onAttentionChange;

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const data = await requestJson<{ result: StrategyRun[] }>("/api/strategies");
      setRuns(data.result);
      report.current?.(data.result.filter(run => run.status === "attention").length);
    } catch (loadError) {
      onNotice({ tone: "error", text: errorMessage(loadError) });
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [onNotice]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => { void load(true); }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const counts = useMemo(() => {
    const result = {} as Record<FilterId, number>;
    for (const item of FILTERS) result[item.id] = runs.filter(run => item.match(run.status)).length;
    return result;
  }, [runs]);

  const visible = useMemo(() => {
    const active = FILTERS.find(item => item.id === filter) ?? FILTERS[0];
    return runs.filter(run => active.match(run.status));
  }, [filter, runs]);

  const needsAttention = counts.attention ?? 0;
  const { barRef: filterBar, pill: filterPill } = useSlidingPill(filter, '[aria-pressed="true"]');

  async function copyRunId(run: StrategyRun) {
    try {
      await navigator.clipboard.writeText(run.id);
      onNotice({ tone: "ok", text: "Run identifier copied." });
    } catch {
      onNotice({ tone: "warning", text: `Clipboard unavailable. Run identifier: ${run.id}` });
    }
  }

  async function runAction() {
    if (!action || busy) return;
    const { run, kind } = action;
    setBusy(true);
    try {
      if (kind === "cancel") {
        await requestJson(`/api/strategies/${run.id}`, { method: "DELETE" });
        onNotice({ tone: "ok", text: `${run.name} cancelled before entry.` });
      } else if (kind === "delete") {
        await requestJson(`/api/strategies/${run.id}/record`, { method: "DELETE" });
        onNotice({ tone: "ok", text: `${run.name} removed from run history.` });
        setDetail(current => (current?.id === run.id ? null : current));
      } else {
        await requestJson(`/api/strategies/${run.id}/exit`, { method: "POST", body: JSON.stringify({ confirm: true }) });
        onNotice({ tone: "ok", text: `${run.name} exited and verified on Delta.` });
      }
      setAction(null);
      // An open Information panel must not keep showing the pre-action record.
      setDetailToken(token => token + 1);
      void load(true);
    } catch (actionError) {
      onNotice({ tone: "error", text: errorMessage(actionError) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="runs">
      <SectionHeading
        eyebrow="Strategy operations"
        title="Run history"
        description="Every schedule creates a separate immutable run. Use the row menu to read the full record, cancel before entry, exit while live, or delete a settled run."
        actions={
          <button type="button" className="button secondary" onClick={() => void load()} disabled={loading}>
            <IconSwap showB={loading} a={<RefreshCw />} b={<RefreshCw className="spin" />} />
            Refresh
          </button>
        }
      />

      {needsAttention > 0 && (
        <p className="callout tone-error" role="status">
          <AlertTriangle aria-hidden="true" />
          <span>
            <strong>{needsAttention} {needsAttention === 1 ? "run needs" : "runs need"} attention.</strong>
            {" "}An entry or exit did not complete cleanly. Open Information on the run to read the recorded error before retrying.
          </span>
        </p>
      )}

      <Panel className="runs-panel">
        {/* One highlight travels between the filters, so the control shows which
            way the selection moved rather than only where it ended up. The counts
            re-enter when they change, because a run moving into Attention while
            the list is open is exactly the thing worth noticing. */}
        <div className="filter-bar" role="group" aria-label="Filter runs by status" ref={filterBar}>
          {filterPill}
          {FILTERS.map(item => (
            <button
              type="button"
              key={item.id}
              aria-pressed={filter === item.id}
              className="filter-tab"
              onClick={() => setFilter(item.id)}
            >
              {item.label}<span><AnimatedNumber value={String(counts[item.id] ?? 0)} /></span>
            </button>
          ))}
        </div>

        {loading && !runs.length ? (
          <TableSkeleton label="run history" rows={6} />
        ) : visible.length ? (
          <div className="table-scroll t-reveal">
            <table className="data-table runs-table">
              <caption className="visually-hidden">Strategy runs with schedule and status</caption>
              <thead>
                <tr>
                  <th scope="col">Strategy</th>
                  <th scope="col">Status</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Exit</th>
                  <th scope="col">Window</th>
                  <th scope="col">Created</th>
                  <th scope="col"><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {visible.map(run => (
                  <RunRow
                    key={run.id}
                    run={run}
                    onInspect={() => setDetail(run)}
                    onAction={kind => setAction({ run, kind })}
                    onCopyId={() => void copyRunId(run)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Activity />}
            title={filter === "all" ? "No strategy runs yet" : "Nothing in this view"}
            description={filter === "all"
              ? "Scheduling a strategy from the builder creates the first run."
              : "Choose another status filter to see the remaining runs."}
          />
        )}
      </Panel>

      {detail && (
        <RunDetailDialog
          run={detail}
          refreshToken={detailToken}
          onClose={() => setDetail(null)}
          onAction={kind => setAction({ run: detail, kind })}
        />
      )}

      {action && (
        <ConfirmModal
          title={ACTION_COPY[action.kind].title}
          description={ACTION_COPY[action.kind].describe(action.run.name)}
          confirm={ACTION_COPY[action.kind].confirm}
          cancel="Keep it"
          busy={busy}
          onClose={() => setAction(null)}
          onConfirm={() => void runAction()}
        />
      )}
    </div>
  );
}

function RunRow({ run, onInspect, onAction, onCopyId }: {
  run: StrategyRun;
  onInspect: () => void;
  onAction: (kind: ActionKind) => void;
  onCopyId: () => void;
}) {
  const canCancel = ["draft", "scheduled"].includes(run.status);
  const canExit = run.status === "active" || (run.status === "attention" && Boolean(run.entryExecutedAt));

  const items: RowMenuItem[] = [
    {
      id: "information",
      label: "Information",
      hint: "Timing, criteria, fills and P&L",
      icon: <Info />,
      onSelect: onInspect
    },
    ...(canExit ? [{
      id: "exit",
      label: "Exit now",
      hint: "Close recorded fills at market",
      icon: <CircleStop />,
      tone: "danger" as const,
      onSelect: () => onAction("exit")
    }] : []),
    ...(canCancel ? [{
      id: "cancel",
      label: "Cancel schedule",
      hint: "Stop it before any order is placed",
      icon: <Ban />,
      onSelect: () => onAction("cancel")
    }] : []),
    {
      id: "copy",
      label: "Copy run ID",
      hint: run.id.slice(0, 8),
      icon: <Copy />,
      onSelect: onCopyId
    },
    {
      id: "delete",
      label: "Delete run",
      hint: canDeleteRun(run) ? "Erase this run and its records" : "Only once the run has settled",
      icon: <Trash2 />,
      tone: "danger",
      disabled: !canDeleteRun(run),
      onSelect: () => onAction("delete")
    }
  ];

  return (
    <tr className="run-row">
      <th scope="row">
        <button type="button" className="run-name" onClick={onInspect}>
          <StatusDot tone={statusTone(run.status)} />
          <span>
            <strong>{run.name}</strong>
            <small>{run.id.slice(0, 8)}</small>
          </span>
        </button>
      </th>
      <td><StatusChip tone={statusTone(run.status)}>{titleCase(run.status)}</StatusChip></td>
      <td>
        <span className="cell-stack">
          <span>{formatDateTime(run.entryAt)}</span>
          <small>{relativeTime(run.entryAt)}</small>
        </span>
      </td>
      <td>
        <span className="cell-stack">
          <span>{formatDateTime(run.exitAt)}</span>
          <small>{relativeTime(run.exitAt)}</small>
        </span>
      </td>
      <td>{formatDuration(run.entryAt, run.exitAt)}</td>
      <td>{relativeTime(run.createdAt)}</td>
      <td className="row-action">
        <RowMenu label={`Actions for ${run.name}`} items={items} />
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ *
 * Information
 * ------------------------------------------------------------------ */

function readable(value: unknown): string {
  if (value === null || value === undefined || value === "") return EM_DASH;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return /^\d{4}-\d{2}-\d{2}T/.test(value) ? formatTimestamp(value) : titleCase(value);
  return JSON.stringify(value);
}

function notional(order: RunOrder) {
  const filled = toNumber(order.filledSize) ?? 0;
  const price = toNumber(order.averageFillPrice);
  const contract = toNumber(order.contractValue) ?? 1;
  return price === null || filled === 0 ? null : filled * price * contract;
}

function pnlTone(value: number | null): StatusTone {
  if (value === null || value === 0) return "neutral";
  return value > 0 ? "positive" : "negative";
}

/**
 * Everything Supabase recorded for one run, in the order an operator reviews it:
 * what happened and when, what it settled for, what was asked for, and then the
 * order-by-order execution quality.
 */
function RunDetailDialog({ run, refreshToken, onClose, onAction }: {
  run: StrategyRun;
  refreshToken: number;
  onClose: () => void;
  onAction: (kind: ActionKind) => void;
}) {
  const [record, setRecord] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    requestJson<{ result: RunDetail }>(`/api/strategies/${run.id}`)
      .then(data => { if (active) { setRecord(data.result); setFailure(null); } })
      .catch(error => { if (active) setFailure(errorMessage(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshToken, run.id]);

  const status = record?.status ?? run.status;
  const settlement = record?.settlement ?? {};
  const realized = toNumber(settlement.realizedPnl);
  const definition = record?.definition ?? {};
  const legs = definition.legs ?? [];
  const orders = record?.orders ?? [];
  const canExit = status === "active" || (status === "attention" && Boolean(record?.entryExecutedAt ?? run.entryExecutedAt));

  const timing: DetailItem[] = [
    { label: "Run identifier", value: run.id, mono: true },
    { label: "Created", value: formatTimestamp(record?.createdAt ?? run.createdAt) },
    { label: "Entry scheduled", value: formatTimestamp(record?.entryAt ?? run.entryAt) },
    { label: "Entry executed", value: record?.entryExecutedAt ? formatTimestamp(record.entryExecutedAt) : "Not executed" },
    { label: "Exit scheduled", value: formatTimestamp(record?.exitAt ?? run.exitAt) },
    { label: "Exit executed", value: record?.exitExecutedAt ? formatTimestamp(record.exitExecutedAt) : "Not executed" },
    { label: "Scheduled window", value: formatDuration(record?.entryAt ?? run.entryAt, record?.exitAt ?? run.exitAt) },
    {
      label: "Actual time in market",
      value: record?.entryExecutedAt && record?.exitExecutedAt
        ? formatDuration(record.entryExecutedAt, record.exitExecutedAt)
        : record?.entryExecutedAt ? "Still open" : EM_DASH
    },
    { label: "Record updated", value: formatTimestamp(record?.updatedAt) },
    { label: "Saved definition", value: record?.savedStrategyId ?? "Not linked", mono: Boolean(record?.savedStrategyId) }
  ];

  const criteria: DetailItem[] = [
    { label: "Index", value: readable(definition.instrument?.index) },
    { label: "Underlying", value: readable(definition.instrument?.underlying) },
    { label: "Underlying price from", value: readable(definition.instrument?.underlyingFrom) },
    { label: "Strategy type", value: readable(definition.entry?.strategyType) },
    { label: "Square off", value: readable(definition.squareOff) },
    { label: "Risk mode", value: readable(definition.riskMode) },
    { label: "Combined stop loss", value: definition.combinedStopLossPercent ? `${definition.combinedStopLossPercent}% of credit` : EM_DASH },
    { label: "Emergency stop loss", value: definition.emergencyStopLossPercent ? `${definition.emergencyStopLossPercent}% per leg` : EM_DASH },
    { label: "Overall target", value: definition.overallTarget ? decimal(definition.overallTarget) : EM_DASH },
    { label: "Overall stop loss", value: definition.overallStopLoss ? decimal(definition.overallStopLoss) : EM_DASH },
    { label: "Trail to break even", value: readable(definition.trailToBreakEven) },
    { label: "Break even scope", value: readable(definition.breakEvenScope) }
  ];

  const riskState = record?.riskState ?? {};
  const riskItems: DetailItem[] = Object.entries(riskState)
    .filter(([key]) => key !== "legs")
    .map(([key, value]) => ({ label: titleCase(key), value: readable(value) }));

  return (
    <Dialog
      title={record?.name ?? run.name}
      subtitle={<>Run {run.id.slice(0, 8)} · {orders.length} recorded {orders.length === 1 ? "order" : "orders"}</>}
      aside={<StatusChip tone={statusTone(status)}>{titleCase(status)}</StatusChip>}
      onClose={onClose}
      footer={
        <>
          <span className="dialog-foot-note">
            {settlement.settledAt ? `Settled ${relativeTime(settlement.settledAt)}` : "Figures are rebuilt from the recorded fills."}
          </span>
          <span className="dialog-foot-actions">
            {canExit && (
              <button type="button" className="button danger small" onClick={() => onAction("exit")}>
                <CircleStop aria-hidden="true" />Exit now
              </button>
            )}
            {canDeleteRun({ ...run, status, exitExecutedAt: record?.exitExecutedAt ?? run.exitExecutedAt }) && (
              <button type="button" className="button ghost small" onClick={() => onAction("delete")}>
                <Trash2 aria-hidden="true" />Delete run
              </button>
            )}
          </span>
        </>
      }
    >
      {loading && !record ? (
        <TableSkeleton label="run record" rows={8} />
      ) : failure ? (
        <InlineMessage tone="error">{failure}</InlineMessage>
      ) : (
        <>
          {record?.lastError && (
            <DetailSection title="Recorded error">
              <p className="detail-error">{record.lastError}</p>
            </DetailSection>
          )}

          <DetailSection title="Timing" meta={relativeTime(record?.createdAt ?? run.createdAt)}>
            <DetailList items={timing} />
          </DetailSection>

          <DetailSection
            title="Settlement"
            meta={settlement.fullyClosed ? "Fully closed" : orders.length ? "Open or partially closed" : "No fills recorded"}
          >
            {orders.length ? (
              <>
                <div className="detail-tiles">
                  <Tile label="Realized P&L" value={signedDecimal(settlement.realizedPnl)} tone={pnlTone(realized)} suffix="USD" />
                  <Tile label="Entry premium" value={signedDecimal(settlement.entryPremium)} suffix="USD" />
                  <Tile label="Exit premium" value={signedDecimal(settlement.exitPremium)} suffix="USD" />
                  <Tile label="Commissions" value={decimal(settlement.commission)} suffix="USD" />
                  <Tile label="Slippage cost" value={signedDecimal(settlement.slippageCost)} tone={pnlTone(-(toNumber(settlement.slippageCost) ?? 0))} suffix="USD" />
                  <Tile label="Lots filled" value={`${decimal(settlement.filledLots, 0)} / ${decimal(settlement.requestedLots, 0)}`} />
                  <Tile label="Lots closed" value={decimal(settlement.closedLots, 0)} />
                </div>
                {Boolean(settlement.bySymbol?.length) && (
                  <div className="table-scroll">
                    <table className="data-table detail-table">
                      <thead>
                        <tr>
                          <th scope="col">Contract</th>
                          <th scope="col">Entry premium</th>
                          <th scope="col">Exit premium</th>
                          <th scope="col">Commission</th>
                          <th scope="col">Lots in / out</th>
                          <th scope="col">Realized</th>
                        </tr>
                      </thead>
                      <tbody>
                        {settlement.bySymbol?.map(item => (
                          <tr key={item.symbol}>
                            <th scope="row" className="mono">{item.symbol}</th>
                            <td>{signedDecimal(item.entryPremium)}</td>
                            <td>{signedDecimal(item.exitPremium)}</td>
                            <td>{decimal(item.commission)}</td>
                            <td>{decimal(item.entryLots, 0)} / {decimal(item.exitLots, 0)}</td>
                            <td className={pnlTone(toNumber(item.realizedPnl)) === "positive" ? "up" : pnlTone(toNumber(item.realizedPnl)) === "negative" ? "down" : undefined}>
                              {signedDecimal(item.realizedPnl)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="detail-note">
                  Premium is signed cash flow in quote currency: selling collects, buying pays. Realized P&L subtracts
                  commissions and is only complete once every lot is closed.
                </p>
              </>
            ) : (
              <p className="detail-note">This run never placed an order, so there is nothing to settle.</p>
            )}
          </DetailSection>

          <DetailSection title="Criteria" meta={`${legs.length} ${legs.length === 1 ? "leg" : "legs"}`}>
            <DetailList items={criteria} />
            {Boolean(legs.length) && (
              <div className="table-scroll">
                <table className="data-table detail-table">
                  <thead>
                    <tr>
                      <th scope="col">Leg</th>
                      <th scope="col">Side</th>
                      <th scope="col">Strike</th>
                      <th scope="col">Expiry</th>
                      <th scope="col">Lots</th>
                      <th scope="col">Order</th>
                      <th scope="col">Target</th>
                      <th scope="col">Stop</th>
                      <th scope="col">Trail</th>
                      <th scope="col">Re-entry</th>
                    </tr>
                  </thead>
                  <tbody>
                    {legs.map(leg => (
                      <tr key={leg.id}>
                        <th scope="row" className="mono">{leg.id}</th>
                        <td>{titleCase(leg.position)} {titleCase(leg.optionType)}</td>
                        <td>
                          {leg.strikeMode === "exact"
                            ? decimal(leg.exactStrike, 0)
                            : `${leg.strikeMode.toUpperCase()}${leg.strikeSteps ? ` +${leg.strikeSteps}` : ""}`}
                        </td>
                        <td>{leg.expiry}</td>
                        <td>{leg.lots}</td>
                        <td>
                          {titleCase(leg.orderType.replace("_order", ""))}
                          {leg.limitPrice ? ` @ ${decimal(leg.limitPrice)}` : ""}
                        </td>
                        <td>{leg.targetProfit ? decimal(leg.targetProfit) : EM_DASH}</td>
                        <td>{leg.stopLoss ? decimal(leg.stopLoss) : EM_DASH}</td>
                        <td>{leg.trailStop ? decimal(leg.trailStop) : EM_DASH}</td>
                        <td>{leg.reentryOnTarget || leg.reentryOnStop ? `${leg.reentryOnTarget} / ${leg.reentryOnStop}` : EM_DASH}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </DetailSection>

          {Boolean(orders.length) && (
            <DetailSection title="Fills and execution quality" meta="Positive slippage is adverse">
              <div className="table-scroll">
                <table className="data-table detail-table">
                  <thead>
                    <tr>
                      <th scope="col">Phase</th>
                      <th scope="col">Contract</th>
                      <th scope="col">Side</th>
                      <th scope="col">Lots</th>
                      <th scope="col">Avg fill</th>
                      <th scope="col">Reference</th>
                      <th scope="col">Slippage</th>
                      <th scope="col">Fees</th>
                      <th scope="col">Notional</th>
                      <th scope="col">State</th>
                      <th scope="col">Placed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map(order => {
                      const value = notional(order);
                      const slippage = toNumber(order.slippage);
                      return (
                        <tr key={order.id}>
                          <td>{titleCase(order.kind ?? "entry")}</td>
                          <th scope="row" className="mono">
                            <span className="cell-stack">
                              <span>{order.productSymbol ?? EM_DASH}</span>
                              <small>{order.legId}</small>
                            </span>
                          </th>
                          <td>{titleCase(order.side ?? "")}</td>
                          <td>
                            <span className="cell-stack">
                              <span>{decimal(order.filledSize, 0)} filled</span>
                              <small>{decimal(order.size, 0)} requested</small>
                            </span>
                          </td>
                          <td>{decimal(order.averageFillPrice)}</td>
                          <td>{decimal(order.referencePrice)}</td>
                          <td className={slippage === null || slippage === 0 ? undefined : slippage > 0 ? "down" : "up"}>
                            <span className="cell-stack">
                              <span>{signedDecimal(order.slippage)}</span>
                              <small>{order.slippagePercent ? `${signedDecimal(order.slippagePercent, 3)}%` : EM_DASH}</small>
                            </span>
                          </td>
                          <td>{decimal(order.commission)}</td>
                          <td>
                            <span className="cell-stack">
                              <span>{value === null ? EM_DASH : decimal(value)}</span>
                              <small>lot {decimal(order.contractValue, 4)}</small>
                            </span>
                          </td>
                          <td>{titleCase(order.state ?? "")}</td>
                          <td>
                            <span className="cell-stack">
                              <span>{formatDateTime(order.createdAt)}</span>
                              <small className="mono">{order.deltaOrderId || order.clientOrderId}</small>
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </DetailSection>
          )}

          {Boolean(record?.executions.length) && (
            <DetailSection title="Execution attempts">
              <ul className="detail-timeline">
                {record?.executions.map(item => (
                  <li key={item.id}>
                    <StatusDot tone={item.status === "completed" ? "positive" : item.status === "running" ? "active" : "negative"} />
                    <div>
                      <strong>{titleCase(item.kind)} · {titleCase(item.status)}</strong>
                      <small>
                        Started {formatTimestamp(item.startedAt)}
                        {item.completedAt ? ` · completed ${formatTimestamp(item.completedAt)}` : " · not completed"}
                      </small>
                      {item.error && <p className="detail-error">{item.error}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </DetailSection>
          )}

          {Boolean(riskItems.length) && (
            <DetailSection
              title="Risk monitor"
              meta={record?.riskMonitoredAt ? `Checked ${relativeTime(record.riskMonitoredAt)}` : undefined}
            >
              <DetailList items={[
                ...riskItems,
                { label: "Last checked", value: formatTimestamp(record?.riskMonitoredAt) },
                { label: "Combined stop triggered", value: record?.combinedStopTriggeredAt ? formatTimestamp(record.combinedStopTriggeredAt) : "Not triggered" }
              ]} />
            </DetailSection>
          )}

          <DetailSection title="Raw metadata">
            <details className="detail-raw">
              <summary>Definition, risk state and settlement JSON</summary>
              <pre>{JSON.stringify({ definition, riskState, settlement }, null, 2)}</pre>
            </details>
            {orders.map(order => (
              <details className="detail-raw" key={`raw-${order.id}`}>
                <summary>{titleCase(order.kind ?? "entry")} order response · {order.productSymbol}</summary>
                <pre>{JSON.stringify(order.response, null, 2)}</pre>
              </details>
            ))}
          </DetailSection>
        </>
      )}
    </Dialog>
  );
}

function Tile({ label, value, suffix, tone = "neutral" }: {
  label: string;
  value: string;
  suffix?: string;
  tone?: StatusTone;
}) {
  return (
    <div className={`detail-tile tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}{suffix && <small>{suffix}</small>}</strong>
    </div>
  );
}
