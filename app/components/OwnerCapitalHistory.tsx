"use client";

import { useState } from "react";
import { useCurrency } from "@/app/components/currency";
import { Segmented } from "@/app/components/ui";
import { formatTimestamp } from "@/lib/format";
import { allocationModeText, observationLabel } from "@/lib/reporting";
import type { CapitalObservation } from "@/lib/app-types";
import { capitalRuleText } from "@/lib/capital";

const PAGE_SIZE = 6;
const OPTIONS = [
  { value: "all", label: "All" },
  { value: "wallet", label: "Wallet" },
  { value: "policy", label: "Policy" },
  { value: "run_allocation", label: "Allocations" }
] satisfies { value: CapitalObservation["kind"] | "all"; label: string }[];

export default function OwnerCapitalHistory({ observations }: { observations: CapitalObservation[] }) {
  const [kind, setKind] = useState<CapitalObservation["kind"] | "all">("all");
  const [page, setPage] = useState(0);
  const { currencyCode, formatMoneyNumber } = useCurrency();
  const filtered = observations.filter(item => kind === "all" || item.kind === kind);
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1));
  const start = currentPage * PAGE_SIZE;
  const visible = filtered.slice(start, start + PAGE_SIZE);

  return <section className="owner-capital-history" aria-label="Recorded capital history">
    <div className="owner-history-heading">
      <div><h3>Recorded capital history</h3><p className="field-hint">Latest {observations.length} recorded observations · account-wide</p></div>
      <Segmented label="Capital observation type" value={kind} options={OPTIONS} onChange={value => {
        const option = OPTIONS.find(item => item.value === value);
        if (option) { setKind(option.value); setPage(0); }
      }} />
    </div>
    {visible.length ? <ol className="capital-history">
      {visible.map((item, index) => <li key={`${item.kind}:${item.source}:${item.observedAt}:${item.runId ?? start + index}`} data-kind={item.kind}>
        <span className="owner-history-node" aria-hidden="true" />
        <span className="cell-stack">
          <strong>{observationLabel(item)}</strong>
          <small><time dateTime={item.observedAt}>{formatTimestamp(item.observedAt)}</time></small>
        </span>
        <span className="capital-history-value">
          {item.kind === "wallet" && <>{formatMoneyNumber(item.totalBalance)} <small>{currencyCode}</small>
            {item.availableBalance !== null && <small>{formatMoneyNumber(item.availableBalance)} available</small>}</>}
          {item.kind === "run_allocation" && <>{formatMoneyNumber(item.allocatedBudget)} <small>{currencyCode}</small></>}
          {item.kind === "policy" && <>{allocationModeText(item.allocationMode)}
            {item.allocationMode === "fixed_amount" && <small>{formatMoneyNumber(item.capitalAmount)} {currencyCode}</small>}
            {item.policy && <>
              <small>Default {capitalRuleText(item.policy)}</small>
              {(["BTC", "ETH"] as const).map(asset => {
                const rule = item.policy?.assetOverrides?.[asset];
                const cap = item.policy?.assetLimits?.[asset];
                return rule || cap ? <small key={asset}>{asset} {rule ? capitalRuleText(rule) : "uses default"}
                  {rule?.allocationMode === "fixed_amount" ? ` ${formatMoneyNumber(rule.capitalAmount)} ${currencyCode}` : ""}
                  {cap ? ` · cap ${cap.mode === "percentage" ? `${cap.value}%` : `${formatMoneyNumber(cap.value)} ${currencyCode}`}` : ""}</small> : null;
              })}
              {item.policy.overallLimit && <small>Overall cap {item.policy.overallLimit.mode === "percentage"
                ? `${item.policy.overallLimit.value}%` : `${formatMoneyNumber(item.policy.overallLimit.value)} ${currencyCode}`}</small>}
              {item.policy.maximumConcurrentStrategies && <small>Up to {item.policy.maximumConcurrentStrategies} strategies</small>}
            </>}
          </>}
        </span>
      </li>)}
    </ol> : <p className="detail-note">{observations.length ? "No observations of this type in the latest records."
      : "No capital observations recorded yet. Older balances are not estimated."}</p>}
    {filtered.length > PAGE_SIZE && <div className="owner-pager">
      <button type="button" className="button ghost small" disabled={currentPage === 0}
        onClick={() => setPage(currentPage - 1)}>Newer</button>
      <span role="status">{start + 1}–{Math.min(start + PAGE_SIZE, filtered.length)} of {filtered.length}</span>
      <button type="button" className="button ghost small" disabled={start + PAGE_SIZE >= filtered.length}
        onClick={() => setPage(currentPage + 1)}>Older</button>
    </div>}
  </section>;
}
