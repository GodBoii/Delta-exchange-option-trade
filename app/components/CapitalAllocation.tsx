"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleDollarSign, RefreshCw, Save } from "@/app/components/icons";
import type { CapitalAllocationMode, CapitalOverview } from "@/lib/app-types";
import { requestJson } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { useCurrency } from "@/app/components/currency";
import {
  IconSwap, InlineMessage, Meter, NumberField, Panel, PanelHeader, Select, Shimmer,
  type NoticeHandler, type SelectOption
} from "@/app/components/ui";

/**
 * Every rule is one sentence about what it does to the balance, so the choice
 * can be made from the list itself instead of from the paragraph that used to
 * sit underneath the form explaining all five.
 */
const MODE_OPTIONS: SelectOption[] = [
  { value: "full_balance", label: "100% per strategy", hint: "One live strategy at a time" },
  { value: "half_balance", label: "50% per strategy", hint: "Up to two live strategies" },
  { value: "one_third_balance", label: "33.33% per strategy", hint: "Up to three live strategies" },
  { value: "one_quarter_balance", label: "25% per strategy", hint: "Up to four live strategies" },
  { value: "fixed_amount", label: "Fixed USD amount", hint: "A flat budget you set below" }
];

function isCapitalAllocationMode(value: string): value is CapitalAllocationMode {
  return MODE_OPTIONS.some(option => option.value === value);
}

/**
 * Capital policy.
 *
 * One account-wide budget, so this is a single panel inside Portfolio rather
 * than a destination of its own: the numbers it needs — total balance, what is
 * available — are the numbers already on this screen, and a whole route for one
 * setting made the operator leave the data to change the rule derived from it.
 *
 * The five facts read as one row of figures instead of two panels of definition
 * lists, because they are all read together when deciding the rule and none of
 * them is worth a heading.
 */
export default function CapitalAllocation({ onNotice }: { onNotice: NoticeHandler }) {
  const { currencyCode, convertFromUsd, convertToUsd, formatMoney } = useCurrency();
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
      setCapitalAmount(convertFromUsd(data.settings.capitalAmount ?? 1));
      setError("");
    } catch (loadError) {
      setError(errorMessage(loadError, "Capital settings could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, [convertFromUsd]);

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
          capitalAmount: mode === "fixed_amount" ? convertToUsd(capitalAmount) : null
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
      || (mode === "fixed_amount" && Math.abs((overview.settings.capitalAmount ?? 0) - convertToUsd(capitalAmount)) > 0.00001);

  const pending = loading ? "Loading" : "Unavailable";
  const modeOptions = MODE_OPTIONS.map(option => option.value === "fixed_amount"
    ? { ...option, label: `Fixed ${currencyCode} amount` } : option);
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
              className="button ghost small"
              onClick={() => void load()}
              disabled={loading || saving}
              aria-label="Reload capital policy"
            >
              <IconSwap showB={loading} a={<RefreshCw />} b={<RefreshCw className="spin" />} />
            </button>
            {/* The save control only exists once there is an unsaved change, so a
                settled policy does not present an action with nothing to do. */}
            {dirty && (
              <button type="button" className="button primary small" onClick={() => void save()} disabled={saving}>
                <Save aria-hidden="true" />{saving ? <Shimmer>Saving</Shimmer> : "Save"}
              </button>
            )}
          </>
        }
      />

      {error && <InlineMessage tone="error">{error}</InlineMessage>}

      <div className="capital-body">
        <div className="capital-rule">
          <Select
            label="Budget per strategy"
            value={mode}
            options={modeOptions}
            onChange={value => { if (isCapitalAllocationMode(value)) setMode(value); }}
            hint={`Calculated from the total USD balance. Displayed in ${currencyCode}, then capped by the balance actually available.`}
          />
          {mode === "fixed_amount" && (
            <NumberField
              label="Amount"
              value={capitalAmount}
              min={0.01}
              step={0.01}
              suffix={currencyCode}
              invalid={capitalAmount <= 0}
              onChange={setCapitalAmount}
            />
          )}
        </div>

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
            <dt>Budget per strategy</dt>
            <dd>{overview ? formatMoney(overview.nominalBudgetPerStrategy) : pending}</dd>
          </div>
          <div>
            <dt>Next strategy can use</dt>
            <dd>{overview ? formatMoney(overview.availableBudgetForNextStrategy) : pending}</dd>
          </div>
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
                : `${overview.availableAllocations} of ${overview.maximumConcurrentStrategies} allocations free for a new entry.`}
          </p>
        </div>
      </div>
    </Panel>
  );
}
