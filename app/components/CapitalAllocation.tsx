"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleDollarSign, RefreshCw, Save, ShieldCheck, WalletCards } from "lucide-react";
import type { CapitalAllocationMode, CapitalOverview } from "@/lib/app-types";
import { requestJson } from "@/lib/api";
import { errorMessage, money } from "@/lib/format";
import {
  InlineMessage, Meter, NumberField, Panel, PanelHeader, SectionHeading, Select, Shimmer,
  type NoticeHandler
} from "@/app/components/ui";

const MODE_OPTIONS = [
  { value: "full_balance", label: "100% per strategy" },
  { value: "half_balance", label: "50% per strategy" },
  { value: "one_third_balance", label: "33.33% per strategy" },
  { value: "one_quarter_balance", label: "25% per strategy" },
  { value: "fixed_amount", label: "Fixed USD amount" }
];

function isCapitalAllocationMode(value: string): value is CapitalAllocationMode {
  return MODE_OPTIONS.some(option => option.value === value);
}

export default function CapitalAllocation({ onNotice }: { onNotice: NoticeHandler }) {
  const [overview, setOverview] = useState<CapitalOverview | null>(null);
  const [mode, setMode] = useState<CapitalAllocationMode>("half_balance");
  const [capitalAmount, setCapitalAmount] = useState(1);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await requestJson<CapitalOverview>("/api/capital/settings");
      setOverview(data);
      setMode(data.settings.allocationMode);
      setCapitalAmount(data.settings.capitalAmount ?? 1);
      setError("");
    } catch (loadError) {
      setError(errorMessage(loadError, "Capital settings could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function save() {
    if (mode === "fixed_amount" && capitalAmount <= 0) {
      setError("Enter a fixed capital amount greater than zero.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const data = await requestJson<CapitalOverview>("/api/capital/settings", {
        method: "PUT",
        body: JSON.stringify({
          allocationMode: mode,
          capitalAmount: mode === "fixed_amount" ? capitalAmount : null
        })
      });
      setOverview(data);
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
    : overview.settings.allocationMode !== mode
      || (mode === "fixed_amount" && overview.settings.capitalAmount !== capitalAmount);

  return (
    <div className="capital-page">
      <SectionHeading
        eyebrow="Account policy"
        title="Capital allocation"
        description="Set one capital budget for every manual and automated strategy. Strategy Builder keeps trade-specific risk controls separate."
        actions={
          <>
            <button type="button" className="button secondary" onClick={() => void load()} disabled={loading || saving}>
              <RefreshCw className={loading ? "spin" : ""} aria-hidden="true" />Refresh
            </button>
            <button type="button" className="button primary" onClick={() => void save()} disabled={loading || saving || !dirty}>
              <Save aria-hidden="true" />{saving ? <Shimmer>Saving</Shimmer> : "Save policy"}
            </button>
          </>
        }
      />

      {error && <InlineMessage tone="error">{error}</InlineMessage>}

      <div className="capital-summary">
        <Panel>
          <PanelHeader icon={<WalletCards />} title="Trading capital" meta="Delta USD wallet" />
          <dl className="capital-facts">
            <div><dt>Total balance</dt><dd>{overview ? money(overview.wallet.totalBalance) : "Loading"}</dd></div>
            <div><dt>Available now</dt><dd>{overview ? money(overview.wallet.availableBalance) : "Loading"}</dd></div>
            <div><dt>Budget per strategy</dt><dd>{overview ? money(overview.nominalBudgetPerStrategy) : "Loading"}</dd></div>
          </dl>
        </Panel>

        <Panel>
          <PanelHeader
            icon={<ShieldCheck />}
            title="Live allocations"
            meta={overview ? `${overview.occupiedAllocations} of ${overview.maximumConcurrentStrategies} in use` : "Loading"}
          />
          <Meter
            value={overview?.occupiedAllocations ?? 0}
            max={overview?.maximumConcurrentStrategies ?? 2}
            label="Capital allocations currently in use"
            tone={overview?.availableAllocations === 0 ? "warning" : "positive"}
          />
          <dl className="capital-facts">
            <div><dt>Maximum simultaneous strategies</dt><dd>{overview?.maximumConcurrentStrategies ?? "Loading"}</dd></div>
            <div><dt>Available allocations</dt><dd>{overview?.availableAllocations ?? "Loading"}</dd></div>
            <div><dt>Next strategy can use</dt><dd>{overview ? money(overview.availableBudgetForNextStrategy) : "Loading"}</dd></div>
          </dl>
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          icon={<CircleDollarSign />}
          title="Allocation rule"
          meta="Applied when an entry starts"
        />
        <div className="capital-form">
          <Select
            label="Capital budget"
            value={mode}
            options={MODE_OPTIONS}
            onChange={value => { if (isCapitalAllocationMode(value)) setMode(value); }}
            hint="The percentage is calculated from total USD balance and capped by currently available balance."
          />
          {mode === "fixed_amount" && (
            <NumberField
              label="Amount per strategy"
              value={capitalAmount}
              min={0.01}
              step={0.01}
              suffix="USD"
              invalid={capitalAmount <= 0}
              hint="The number of simultaneous allocations is calculated from total balance."
              onChange={setCapitalAmount}
            />
          )}
        </div>
        <p className="capital-explanation">
          With the default 50% rule, the first live strategy can use half of the account balance. A second strategy can use the remaining half. No third entry can start until one allocation is released.
        </p>
      </Panel>
    </div>
  );
}
