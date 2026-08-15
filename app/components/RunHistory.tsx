"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, CircleStop, RefreshCw, Trash2 } from "lucide-react";
import { requestJson } from "@/lib/api";
import { errorMessage, formatDateTime, formatDuration, relativeTime, titleCase } from "@/lib/format";
import type { StrategyRun } from "@/lib/app-types";
import {
  ConfirmModal, EmptyState, Panel, SectionHeading, StatusChip, StatusDot, TableSkeleton,
  type NoticeHandler, type StatusTone
} from "@/app/components/ui";

const AUTO_REFRESH_MS = 30_000;

type FilterId = "all" | "pending" | "live" | "attention" | "closed";

const FILTERS: { id: FilterId; label: string; match: (status: string) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "pending", label: "Scheduled", match: status => ["draft", "scheduled"].includes(status) },
  { id: "live", label: "Live", match: status => ["active", "executing_entry", "executing_exit"].includes(status) },
  { id: "attention", label: "Attention", match: status => status === "attention" },
  { id: "closed", label: "Closed", match: status => ["completed", "cancelled", "expired"].includes(status) }
];

function statusTone(status: string): StatusTone {
  if (status === "attention") return "negative";
  if (status === "completed") return "positive";
  if (status === "active" || status.startsWith("executing")) return "active";
  if (status === "scheduled" || status === "draft") return "warning";
  return "neutral";
}

/**
 * Operational log of scheduled runs.
 *
 * Runs are immutable records, so this surface only offers the two reversals the
 * backend supports: cancel before entry, or exit an already-live strategy.
 */
export default function RunHistory({ onNotice }: { onNotice: NoticeHandler }) {
  const [runs, setRuns] = useState<StrategyRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterId>("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [action, setAction] = useState<{ run: StrategyRun; kind: "cancel" | "exit" } | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const data = await requestJson<{ result: StrategyRun[] }>("/api/strategies");
      setRuns(data.result);
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

  async function runAction() {
    if (!action) return;
    const { run, kind } = action;
    try {
      if (kind === "cancel") {
        await requestJson(`/api/strategies/${run.id}`, { method: "DELETE" });
        onNotice({ tone: "ok", text: `${run.name} cancelled before entry.` });
      } else {
        await requestJson(`/api/strategies/${run.id}/exit`, { method: "POST", body: JSON.stringify({ confirm: true }) });
        onNotice({ tone: "ok", text: `${run.name} exited and verified on Delta.` });
      }
      setAction(null);
      void load(true);
    } catch (actionError) {
      onNotice({ tone: "error", text: errorMessage(actionError) });
    }
  }

  return (
    <div className="runs">
      <SectionHeading
        eyebrow="Strategy operations"
        title="Run history"
        description="Every schedule creates a separate immutable run. Cancel before entry, or exit while live."
        actions={
          <button type="button" className="button secondary" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={loading ? "spin" : ""} aria-hidden="true" />Refresh
          </button>
        }
      />

      {needsAttention > 0 && (
        <p className="callout tone-error" role="status">
          <AlertTriangle aria-hidden="true" />
          <span>
            <strong>{needsAttention} {needsAttention === 1 ? "run needs" : "runs need"} attention.</strong>
            {" "}An entry or exit did not complete cleanly. Open the run to read the recorded error before retrying.
          </span>
        </p>
      )}

      <Panel className="runs-panel">
        <div className="filter-bar" role="group" aria-label="Filter runs by status">
          {FILTERS.map(item => (
            <button
              type="button"
              key={item.id}
              aria-pressed={filter === item.id}
              className="filter-tab"
              onClick={() => setFilter(item.id)}
            >
              {item.label}<span>{counts[item.id] ?? 0}</span>
            </button>
          ))}
        </div>

        {loading && !runs.length ? (
          <TableSkeleton label="run history" rows={6} />
        ) : visible.length ? (
          <div className="table-scroll">
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
                {visible.map(run => {
                  const canCancel = ["draft", "scheduled"].includes(run.status);
                  const canExit = run.status === "active" || (run.status === "attention" && Boolean(run.entryExecutedAt));
                  const open = expanded === run.id;
                  return (
                    <RunRow
                      key={run.id}
                      run={run}
                      open={open}
                      canCancel={canCancel}
                      canExit={canExit}
                      onToggle={() => setExpanded(open ? null : run.id)}
                      onAction={kind => setAction({ run, kind })}
                    />
                  );
                })}
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

      {action && (
        <ConfirmModal
          title={action.kind === "cancel" ? "Cancel scheduled strategy?" : "Exit live strategy?"}
          description={action.kind === "cancel"
            ? `${action.run.name} will be cancelled before entry and no orders will be placed.`
            : `${action.run.name} will cancel still-open entry orders, submit reduce-only market closes for recorded fills, and verify the live Delta positions.`}
          confirm={action.kind === "cancel" ? "Cancel strategy" : "Exit strategy"}
          cancel="Keep it"
          onClose={() => setAction(null)}
          onConfirm={() => void runAction()}
        />
      )}
    </div>
  );
}

function RunRow({ run, open, canCancel, canExit, onToggle, onAction }: {
  run: StrategyRun;
  open: boolean;
  canCancel: boolean;
  canExit: boolean;
  onToggle: () => void;
  onAction: (kind: "cancel" | "exit") => void;
}) {
  return (
    <>
      <tr className={open ? "run-row open" : "run-row"}>
        <th scope="row">
          <button type="button" className="run-name" onClick={onToggle} aria-expanded={open}>
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
          {canCancel && (
            <button type="button" className="button ghost small" onClick={() => onAction("cancel")}>
              <Trash2 aria-hidden="true" />Cancel
            </button>
          )}
          {canExit && (
            <button type="button" className="button danger small" onClick={() => onAction("exit")}>
              <CircleStop aria-hidden="true" />Exit
            </button>
          )}
          {!canCancel && !canExit && <span className="row-action-empty">No action</span>}
        </td>
      </tr>
      {open && (
        <tr className="run-detail-row">
          <td colSpan={7}>
            <dl className="run-detail">
              <div><dt>Run identifier</dt><dd className="mono">{run.id}</dd></div>
              <div><dt>Created</dt><dd>{formatDateTime(run.createdAt)}</dd></div>
              <div><dt>Entry executed</dt><dd>{run.entryExecutedAt ? formatDateTime(run.entryExecutedAt) : "Not executed"}</dd></div>
              {run.lastError && (
                <div className="run-detail-error">
                  <dt>Recorded error</dt>
                  <dd>{run.lastError}</dd>
                </div>
              )}
            </dl>
          </td>
        </tr>
      )}
    </>
  );
}
