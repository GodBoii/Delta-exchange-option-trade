"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CircleDollarSign, RefreshCw, Save } from "@/app/components/icons";
import type { AgentAsset, CapitalOverview, CapitalPreview, CapitalSettings } from "@/lib/app-types";
import { requestJson, type ApiRequester } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { capitalSettingsError, normalizeCapitalSettings } from "@/lib/capital";
import CapitalPolicyFields from "@/app/components/CapitalPolicyFields";
import { useCurrency } from "@/app/components/currency";
import { InlineMessage, Segmented, Shimmer, SpinningCounter, type NoticeHandler } from "@/app/components/ui";

export default function CapitalAllocation({ onNotice, request = requestJson, compact = false, onManage, refreshKey = 0 }: {
  onNotice: NoticeHandler; request?: ApiRequester; compact?: boolean; onManage?: () => void; refreshKey?: number;
}) {
  const { formatMoney } = useCurrency();
  const [overview, setOverview] = useState<CapitalOverview | null>(null);
  const [draft, setDraft] = useState<CapitalSettings>(() => normalizeCapitalSettings({ allocationMode: "half_balance" }));
  const [asset, setAsset] = useState<AgentAsset>("BTC");
  const [preview, setPreview] = useState<CapitalPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const generation = useRef(0);
  const hasEdits = useRef(false);
  const load = useCallback(async (preserveEdits = false) => {
    const current = ++generation.current;
    setLoading(true);
    try {
      const data = await request<CapitalOverview>("/api/capital/settings");
      if (current !== generation.current) return;
      if (preserveEdits && hasEdits.current) {
        // Keep the original revision with an unsaved draft, so concurrent policy edits still conflict on save.
        setOverview(current => current ? { ...data, settings: current.settings, capitalRevision: current.capitalRevision } : data);
      } else {
        setOverview(data);
        setDraft(normalizeCapitalSettings(data.settings));
        hasEdits.current = false;
      }
      setError("");
    } catch (cause) {
      if (current === generation.current) setError(errorMessage(cause, "Capital settings could not be loaded."));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, [request]);
  const invalidateLoad = useCallback(() => { generation.current++; }, []);
  useEffect(() => { void load(true); return invalidateLoad; }, [load, refreshKey, invalidateLoad]);
  const invalid = capitalSettingsError(draft);
  const dirty = !!overview && JSON.stringify(normalizeCapitalSettings(overview.settings)) !== JSON.stringify(normalizeCapitalSettings(draft));

  useEffect(() => {
    if (compact || !overview || invalid || loading) { setPreviewing(false); return; }
    let active = true;
    const controller = new AbortController();
    setPreviewing(true);
    setPreviewError("");
    const timer = window.setTimeout(() => {
      void request<CapitalPreview>("/api/capital/preview", {
        method: "POST", body: JSON.stringify({ ...draft, asset }), signal: controller.signal,
      }).then(data => { if (active) setPreview(data); })
        .catch(cause => { if (active) { setPreview(null); setPreviewError(errorMessage(cause, "The entry estimate is unavailable.")); } })
        .finally(() => { if (active) setPreviewing(false); });
    }, 300);
    return () => { active = false; controller.abort(); window.clearTimeout(timer); };
  }, [asset, compact, draft, invalid, loading, overview, request]);

  async function save() {
    if (invalid || saving || !overview) return;
    setSaving(true);
    setError("");
    try {
      const data = await request<CapitalOverview>("/api/capital/settings", {
        method: "PUT", body: JSON.stringify({ ...draft, expectedRevision: overview.capitalRevision ?? null }),
      });
      setOverview(data);
      setDraft(normalizeCapitalSettings(data.settings));
      hasEdits.current = false;
      onNotice({ tone: "ok", text: "Budget saved for future entries. Open runs keep their current budgets." });
    } catch (cause) { setError(errorMessage(cause, "The budget could not be saved.")); }
    finally { setSaving(false); }
  }

  if (compact) return <section className="capital-quick" aria-label="Capital and accounts">
    <span className="capital-quick-icon"><CircleDollarSign aria-hidden="true" /></span>
    <div><h2>Capital & accounts</h2><p>{overview
      ? `${formatMoney(overview.wallet.totalBalance)} ${overview.walletScope === "managed_pool" ? "in managed capital" : "in main wallet"} · ${overview.occupiedAllocations} of ${overview.maximumConcurrentStrategies} strategy slots held`
      : loading ? "Reading your trading capital" : "Capital settings unavailable"}</p>
      {error && <InlineMessage tone="error">{error}</InlineMessage>}
    </div>
    <button className="button secondary" type="button" onClick={onManage}>Manage capital<ArrowRight aria-hidden="true" /></button>
  </section>;

  const figures = !invalid && preview ? preview : overview;
  const proposed = figures?.assetBudgets[asset];
  const route = !previewing && !invalid ? preview?.entryPreview : null;
  const total = figures?.wallet.totalBalance ?? 0;
  const reserved = figures?.reservedBudget ?? 0;
  const next = proposed?.availableBudgetForNextStrategy ?? 0;
  const heldPercent = total > 0 ? Math.min(100, reserved / total * 100) : 0;
  const nextPercent = total > 0 ? Math.max(0, Math.min(100 - heldPercent, next / total * 100)) : 0;
  const stamp = invalid ? "Check your settings" : previewing ? "Updating estimate" : dirty ? "Unsaved proposal" : "Saved settings";

  return <section className="capital-studio" aria-label="Strategy budget">
    <h2 className="visually-hidden">Strategy budget</h2>
    <div className="capital-scope-strip">
      <div><span>{overview?.walletScope === "managed_pool" ? "Managed trading capital" : "Main account capital"}</span>
        <strong>{overview ? formatMoney(overview.wallet.totalBalance) : "Reading balance"}</strong></div>
      <p>{overview?.walletScope === "managed_pool" ? "Main + connected participating sub-accounts. Busy accounts stay included." : "Percentages use the total main wallet balance, before available-margin limits."}</p>
      <button type="button" className="button ghost icon-only" aria-label="Reload capital and discard unsaved edits" disabled={loading || saving}
        onClick={() => void load()}><RefreshCw className={loading ? "spin" : undefined} /></button>
    </div>
    {error && <InlineMessage tone="error">{error}</InlineMessage>}
    <div className="budget-mobile-summary" aria-live="polite"><span>{previewing ? "Updating estimate" : `Next ${asset} budget`}<strong>{figures && !invalid ? formatMoney(next) : "—"}</strong></span><a href="#budget-entry-estimate">View estimate<ArrowRight aria-hidden="true" /></a></div>
    <div className="capital-studio-grid">
      <div className="capital-settings">
        <CapitalPolicyFields value={draft} onChange={next => {
          hasEdits.current = !!overview && JSON.stringify(normalizeCapitalSettings(next)) !== JSON.stringify(normalizeCapitalSettings(overview.settings));
          setDraft(next);
        }} disabled={loading || saving || !overview} totalBalance={overview?.wallet.totalBalance} />
        {invalid && <InlineMessage tone="error">{invalid}</InlineMessage>}
        <div className="budget-save-bar">
          <span>{dirty ? "Changes apply to future entries" : <><Check aria-hidden="true" />All changes saved</>}</span>
          {dirty && <button type="button" className="button ghost" disabled={saving} onClick={() => { if (overview) { setDraft(normalizeCapitalSettings(overview.settings)); hasEdits.current = false; } }}>Discard</button>}
          <button className="button primary" type="button" disabled={!dirty || saving || loading || !!invalid} onClick={() => void save()}>
            <Save aria-hidden="true" />{saving ? <Shimmer>Saving</Shimmer> : "Save budget"}
          </button>
        </div>
      </div>
      <aside id="budget-entry-estimate" className="budget-receipt" aria-label="Next strategy estimate" aria-busy={previewing || loading}>
        <div className="receipt-heading"><span>Before the next entry</span><span className="receipt-stamp" data-dirty={dirty}>{stamp}</span></div>
        <Segmented label="Preview asset" value={asset} options={[{ value: "BTC", label: "BTC" }, { value: "ETH", label: "ETH" }]}
          onChange={value => { if (value === "BTC" || value === "ETH") setAsset(value); }} />
        <div className="budget-orbit" data-pending={previewing || !!invalid}>
          <svg viewBox="0 0 240 240" role="img" aria-label={`${formatMoney(reserved)} reserved. ${formatMoney(next)} estimated next budget out of ${formatMoney(total)} trading capital.`}>
            <circle className="orbit-track" cx="120" cy="120" r="100" />
            <circle className="orbit-held" cx="120" cy="120" r="100" pathLength="100" strokeDasharray={`${heldPercent} 100`} />
            <circle className="orbit-next" cx="120" cy="120" r="100" pathLength="100" strokeDasharray={`${nextPercent} 100`} strokeDashoffset={-heldPercent} />
          </svg>
          <div className="budget-orbit-value"><span>Next {asset} budget</span><strong><SpinningCounter value={figures && !invalid ? formatMoney(next) : "—"} /></strong>
            <small>{proposed ? `${formatMoney(proposed.nominalBudgetPerStrategy)} requested` : "Waiting for wallet"}</small></div>
        </div>
        <div className="orbit-key"><span><i />Next strategy</span><span><i />Already reserved</span></div>
        <dl className="receipt-facts">
          <div><dt>Available across this scope</dt><dd>{figures ? formatMoney(figures.wallet.availableBalance) : "—"}</dd></div>
          <div><dt>Reserved strategy budgets</dt><dd>{figures ? formatMoney(reserved) : "—"}</dd></div>
          <div><dt>Maximum total allocated</dt><dd>{figures ? formatMoney(figures.overallCapitalLimit) : "—"}</dd></div>
          <div><dt>Strategy slots held</dt><dd>{figures ? `${figures.occupiedAllocations} / ${figures.maximumConcurrentStrategies}` : "—"}</dd></div>
        </dl>
        <div className="receipt-route" aria-live="polite">
          {previewError ? <InlineMessage tone="error">{previewError}</InlineMessage> : invalid ? <p>Fix the highlighted settings to preview this entry.</p> : previewing || !preview ? <p><Shimmer>Checking accounts and funding</Shimmer></p> : route && <>
            <span className="receipt-route-label">{route.status === "blocked" ? "Action needed" : "Estimated account"}</span>
            {route.account && <strong>{route.account.name}<small>Account {route.account.id}</small></strong>}
            <p>{route.reason}</p>
            {route.transfers.length > 0 && <ol className="receipt-transfers">{route.transfers.map((transfer, index) => <li key={`${transfer.source}-${transfer.target}-${index}`}>
              <span>{transfer.source}<ArrowRight aria-hidden="true" />{transfer.target}</span><strong>{formatMoney(Number(transfer.amount))}</strong><small>{transfer.purpose}</small>
            </li>)}</ol>}
            {route.status === "blocked" && <a href={proposed?.reason ? "#capital-budget-settings" : "#account-routing"} className="receipt-link">{proposed?.reason ? "Review budget limits" : "Review accounts & funding"}<ArrowRight aria-hidden="true" /></a>}
          </>}
        </div>
        <p className="receipt-footnote">Estimate only. Entry rechecks funds, account access, contracts, and scheduling. Existing assigned runs retain their account.</p>
      </aside>
    </div>
  </section>;
}
