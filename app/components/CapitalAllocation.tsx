"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleDollarSign, RefreshCw, Save } from "@/app/components/icons";
import type { CapitalOverview, CapitalSettings } from "@/lib/app-types";
import { requestJson, type ApiRequester } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { capitalSettingsError, normalizeCapitalSettings } from "@/lib/capital";
import CapitalPolicyFields from "@/app/components/CapitalPolicyFields";
import { useCurrency } from "@/app/components/currency";
import {
  IconSwap, InlineMessage, Meter, Panel, PanelHeader, Shimmer,
  type NoticeHandler
} from "@/app/components/ui";

/** Account default, BTC/ETH budgets and caps alongside live wallet previews. */
export default function CapitalAllocation({ onNotice, request = requestJson }: { onNotice: NoticeHandler; request?: ApiRequester }) {
  const { formatMoney } = useCurrency();
  const [overview, setOverview] = useState<CapitalOverview | null>(null);
  const [draft, setDraft] = useState<CapitalSettings>(() => normalizeCapitalSettings({ allocationMode: "half_balance" }));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<CapitalOverview>("/api/capital/settings");
      setOverview(data);
      setDraft(normalizeCapitalSettings(data.settings));
      setError("");
    } catch (loadError) {
      setError(errorMessage(loadError, "Capital settings could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => { void load(); }, [load]);

  async function save() {
    const invalid = capitalSettingsError(draft);
    if (invalid) {
      setError(invalid);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const data = await request<CapitalOverview>("/api/capital/settings", {
        method: "PUT",
        body: JSON.stringify({ ...draft, expectedRevision: overview?.capitalRevision ?? null })
      });
      setOverview(data);
      setDraft(normalizeCapitalSettings(data.settings));
      onNotice({
        tone: "ok",
        text: `Capital policy saved. Up to ${data.maximumConcurrentStrategies} strategies can hold allocations.`
      });
    } catch (saveError) {
      setError(errorMessage(saveError, "Capital settings could not be saved."));
    } finally {
      setSaving(false);
    }
  }

  const dirty = overview === null
    ? false
    : JSON.stringify(normalizeCapitalSettings(overview.settings)) !== JSON.stringify(normalizeCapitalSettings(draft));

  const pending = loading ? "Loading" : "Unavailable";
  const allocationsFull = overview?.availableAllocations === 0;

  return (
    <Panel className="capital-panel">
      <PanelHeader
        icon={<CircleDollarSign />}
        title="Capital policy"
        meta={overview
          ? `${overview.occupiedAllocations} of ${overview.maximumConcurrentStrategies} allocations in use`
          : pending}
        actions={
          <>
            <button
              type="button"
              className="button ghost small icon-only"
              onClick={() => void load()}
              disabled={loading || saving}
              aria-label="Reload capital policy"
            >
              <IconSwap showB={loading} a={<RefreshCw />} b={<RefreshCw className="spin" />} />
            </button>
            {/* The save control only exists once there is an unsaved change, so a
                settled policy does not present an action with nothing to do. */}
            {dirty && (
              <button type="button" className="button primary small" onClick={() => void save()} disabled={saving || loading || !!capitalSettingsError(draft)}>
                <Save aria-hidden="true" />{saving ? <Shimmer>Saving</Shimmer> : "Save"}
              </button>
            )}
          </>
        }
      />

      {error && <InlineMessage tone="error">{error}</InlineMessage>}
      {dirty && capitalSettingsError(draft) && <InlineMessage tone="error">{capitalSettingsError(draft)}</InlineMessage>}

      <div className="capital-body">
        <CapitalPolicyFields value={draft} onChange={setDraft} disabled={loading || saving || !overview} />

        <div className="capital-summary">
          <dl className="capital-figures">
            <div>
              <dt>Total balance</dt>
              <dd>{overview ? formatMoney(overview.wallet.totalBalance) : pending}</dd>
            </div>
            <div>
              <dt>Available now</dt>
              <dd>{overview ? formatMoney(overview.wallet.availableBalance) : pending}</dd>
            </div>
            <div>
              <dt>Overall capital cap</dt>
              <dd>{overview ? formatMoney(overview.overallCapitalLimit) : pending}</dd>
            </div>
            <div>
              <dt>Reserved across wallet</dt>
              <dd>{overview ? formatMoney(overview.reservedBudget) : pending}</dd>
            </div>
            {overview && (["BTC", "ETH"] as const).map(asset => <div className="capital-asset-figure" key={asset}>
              <dt>{asset} budget per strategy</dt><dd>{formatMoney(overview.assetBudgets[asset].nominalBudgetPerStrategy)}</dd>
              <dd className="capital-asset-detail">
                <span>Next entry <b>{formatMoney(overview.assetBudgets[asset].availableBudgetForNextStrategy)}</b></span>
                <span>Reserved <b>{formatMoney(overview.assetBudgets[asset].reservedBudget)}</b></span>
                <span>Cap <b>{formatMoney(overview.assetBudgets[asset].capitalLimit)}</b></span>
              </dd>
            </div>)}
          </dl>

          <div className="capital-allocations">
            <Meter
              value={overview?.occupiedAllocations ?? 0}
              max={overview?.maximumConcurrentStrategies ?? 2}
              label="Capital allocations currently in use"
              tone={allocationsFull ? "warning" : "positive"}
            />
            <p>
              {overview === null
                ? "Reading your allocation state."
                : allocationsFull
                  ? "Every allocation is held. No further entry can start until one is released."
                  : `${overview.availableAllocations} strategy slots free. Wallet funds and capital caps also apply.`}
            </p>
          </div>
        </div>
      </div>
    </Panel>
  );
}
