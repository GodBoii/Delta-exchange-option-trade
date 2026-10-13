"use client";

import { useEffect, useState } from "react";
import { Info, RefreshCw } from "@/app/components/icons";
import { useCurrency } from "@/app/components/currency";
import { DetailList, DetailSection, Dialog, InlineMessage, StatusChip, TableSkeleton } from "@/app/components/ui";
import { requestJson } from "@/lib/api";
import type { StrategyRun, TradeMonitorSnapshot } from "@/lib/app-types";
import { decimal, EM_DASH, errorMessage, formatTimestamp, titleCase, toNumber } from "@/lib/format";
import { MONITOR_REFRESH_MS, monitorFinished, monitorMessage, riskObservationStale } from "@/lib/trade-monitor";

export default function TradeMonitor({ run, endpoint, accountName, onClose, onDetails }: {
  run: StrategyRun;
  endpoint: string;
  accountName?: string;
  onClose: () => void;
  onDetails: () => void;
}) {
  const { currencyCode, formatMoneyNumber } = useCurrency();
  const [snapshot, setSnapshot] = useState<TradeMonitorSnapshot | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending = false;
    let finished = false;
    async function poll() {
      if (pending || controller.signal.aborted || document.hidden) return;
      pending = true;
      try {
        const data = await requestJson<TradeMonitorSnapshot>(endpoint, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]), cache: "no-store"
        });
        if (controller.signal.aborted) return;
        if (data.run.id !== run.id) throw new Error("The returned trade does not match the selected run.");
        setSnapshot(data);
        setFailure(null);
        setNow(Date.now());
        finished = monitorFinished(data);
      } catch (error) {
        if (!controller.signal.aborted) { setFailure(errorMessage(error)); setNow(Date.now()); }
      } finally {
        pending = false;
        if (!controller.signal.aborted && !finished && !document.hidden) timer = setTimeout(() => void poll(), MONITOR_REFRESH_MS);
      }
    }
    function visibility() {
      clearTimeout(timer);
      if (!document.hidden && !finished) void poll();
    }
    void poll();
    document.addEventListener("visibilitychange", visibility);
    const clock = setInterval(() => setNow(Date.now()), MONITOR_REFRESH_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
      clearInterval(clock);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [endpoint, refresh, run.id]);

  const record = snapshot?.run;
  const risk = record?.riskState ?? {};
  const finished = snapshot ? monitorFinished(snapshot) : false;
  const disconnected = Boolean(failure) || Boolean(snapshot && now - Date.parse(snapshot.observedAt) > 8_000 && !finished);
  const staleRisk = snapshot ? riskObservationStale(snapshot, now) : true;
  const profit = record?.settlement.fullyClosed ? record.settlement.realizedPnl : risk.profit;
  const profitNumber = toNumber(profit);
  const status = record?.status ?? run.status;
  const exposureClosed = risk.exposureStatus === "flat" || record?.settlement.fullyClosed;
  const money = (value: unknown) => `${formatMoneyNumber(value, { digits: 4 })} ${currencyCode}`;

  return <Dialog title={record?.name ?? run.name} className="trade-monitor"
    subtitle={accountName ? `${accountName} · Trade status` : "Trade status"} onClose={onClose}
    aside={<StatusChip tone={status === "attention" ? "negative" : finished ? "neutral" : "active"}>{titleCase(status)}</StatusChip>}
    footer={<>
      <span className="dialog-foot-note">{finished ? "Final recorded state" : "Refreshes every 2 seconds while visible"}</span>
      <span className="dialog-foot-actions">
        <button type="button" className="button ghost small" onClick={() => setRefresh(value => value + 1)}><RefreshCw aria-hidden="true" />Refresh</button>
        <button type="button" className="button secondary small" onClick={onDetails}><Info aria-hidden="true" />Details</button>
      </span>
    </>}>
    <div className="trade-monitor-status" role="status">
      <StatusChip tone={failure || disconnected ? "warning" : finished ? "neutral" : snapshot ? "positive" : "neutral"}>
        {failure || disconnected ? "Updates interrupted" : finished ? "Recorded result" : snapshot ? "Updating" : "Connecting"}
      </StatusChip>
      <span>{snapshot ? `Last received ${formatTimestamp(snapshot.observedAt)}` : "Loading this trade"}</span>
    </div>
    {failure && <InlineMessage tone="error">{failure}. {snapshot ? "Showing the last received state. Retrying automatically." : "Retrying automatically. Use Refresh to retry now."}</InlineMessage>}
    {!snapshot ? !failure && <TableSkeleton label="trade status" rows={5} /> : <>
      <p className="trade-monitor-message">{monitorMessage(snapshot)}</p>
      {record?.lastError && <InlineMessage tone="error">{errorMessage(new Error(record.lastError))}</InlineMessage>}
      {typeof risk.message === "string" && <InlineMessage tone="warning">{risk.message}</InlineMessage>}
      {!finished && staleRisk && !exposureClosed && <InlineMessage tone="warning">
        {typeof risk.pricedAt === "string" ? "The last risk valuation is over 30 seconds old." : "No strategy risk valuation has been recorded yet."} Contract prices below have their own freshness check.
      </InlineMessage>}
      <DetailSection title="Position and risk" meta={typeof risk.pricedAt === "string" ? `Valued ${formatTimestamp(risk.pricedAt)}` : "Waiting for engine valuation"}>
        <div className="detail-tiles">
          <div className={`detail-tile${profitNumber ? profitNumber > 0 ? " tone-positive" : " tone-negative" : ""}`}>
            <span>{record?.settlement.fullyClosed ? "Realized P&L" : "Gross P&L at last risk check"}</span>
            <strong>{formatMoneyNumber(profit, { digits: 4, signed: true })}</strong><small>{currencyCode}</small>
          </div>
          <div className="detail-tile"><span>Lots filled / closed</span><strong>{decimal(record?.settlement.filledLots, 0)} / {decimal(record?.settlement.closedLots, 0)}</strong></div>
        </div>
        <DetailList items={[
          { label: "Exposure", value: typeof risk.exposureStatus === "string" ? titleCase(risk.exposureStatus) : "Not confirmed" },
          { label: "Risk monitor", value: exposureClosed ? "Closed" : typeof risk.status === "string" ? titleCase(risk.status) : EM_DASH },
          { label: "Risk basis", value: typeof risk.riskBasis === "string" ? titleCase(risk.riskBasis) : EM_DASH },
          { label: "Return at last check", value: toNumber(risk.returnPercent) === null ? EM_DASH : `${decimal(risk.returnPercent, 2)}%` },
          { label: "Stop threshold", value: money(risk.stopValue) },
          { label: "Take-profit threshold", value: money(risk.targetValue) },
          { label: "Entry", value: formatTimestamp(record?.entryExecutedAt ?? record?.entryAt) },
          { label: "Scheduled exit", value: formatTimestamp(record?.exitAt) },
          { label: "Last engine check", value: formatTimestamp(record?.riskMonitoredAt) },
          { label: "Exit reason", value: typeof risk.exitReason === "string" ? titleCase(risk.exitReason) : typeof risk.closureReason === "string" ? titleCase(risk.closureReason) : EM_DASH }
        ]} />
        {!record?.settlement.fullyClosed && <p className="detail-note">Risk valuations follow the engine&apos;s checks, normally about every 10 seconds. Gross P&L excludes fees and is an estimate, not a closing quote.</p>}
      </DetailSection>
      <DetailSection title="Contract prices" meta={finished ? "Live prices stopped" : disconnected ? "Waiting for fresh observations" : snapshot.privateStream === "live" ? "Account stream connected" : "Account stream unavailable"}>
        {snapshot.quotes.length ? <DetailList items={snapshot.quotes.map(quote => ({ label: quote.symbol, value: finished ? "Live prices stopped" : disconnected || quote.markPrice === null ? "Live mark unavailable" : money(quote.markPrice) }))} />
          : <p className="detail-note">Contract prices appear after orders are recorded.</p>}
        <p className="detail-note">Marks come from the existing Delta feed. Prices older than 15 seconds are unavailable. Archived runs have no live quotes.</p>
      </DetailSection>
      <DetailSection title="Orders" meta={`${record?.orders.length ?? 0} recorded`}>
        {record?.orders.length ? <div className="table-scroll mobile-card-list"><table className="data-table mobile-card-table">
          <caption className="visually-hidden">Recorded orders for this trade</caption>
          <thead><tr><th scope="col">Contract</th><th scope="col">Action</th><th scope="col">State</th><th scope="col">Filled / requested</th><th scope="col">Average fill</th></tr></thead>
          <tbody>{record.orders.map(order => <tr key={order.id}>
            <th scope="row" className="mono">{order.productSymbol ?? EM_DASH}</th>
            <td data-label="Action">{titleCase(order.kind)} · {titleCase(order.side ?? "")}</td>
            <td data-label="State">{titleCase(order.state ?? "unknown")}</td>
            <td data-label="Filled / requested">{decimal(order.filledSize, 0)} / {decimal(order.size, 0)}</td>
            <td data-label="Average fill">{money(order.averageFillPrice)}</td>
          </tr>)}</tbody>
        </table></div> : <p className="detail-note">No orders recorded yet.</p>}
      </DetailSection>
      <DetailSection title="Execution activity">
        {record?.executions.length ? <ol className="trade-monitor-activity">{record.executions.map(item => <li key={item.id}>
          <strong>{titleCase(item.kind)} · {titleCase(item.status)}</strong>
          <span>{formatTimestamp(item.completedAt ?? item.startedAt)}</span>
          {item.error && <p className="detail-error">{errorMessage(new Error(item.error))}</p>}
        </li>)}</ol> : <p className="detail-note">Execution activity appears when entry begins.</p>}
      </DetailSection>
    </>}
  </Dialog>;
}
