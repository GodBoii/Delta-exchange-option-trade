"use client";

import { useCurrency } from "@/app/components/currency";
import { NumberField, Segmented, Select } from "@/app/components/ui";
import type { CapitalLimit, CapitalRule, CapitalSettings } from "@/lib/app-types";
import { capitalRulePercent, capitalRuleText, capitalStrategyLimit } from "@/lib/capital";

function RuleFields({ label, rule, onChange }: {
  label: string; rule: CapitalRule; onChange: (rule: CapitalRule) => void;
}) {
  const { currencyCode, convertFromUsd, convertToUsd } = useCurrency();
  const percentage = capitalRulePercent(rule);
  const fixed = rule.allocationMode === "fixed_amount";
  return <div className="budget-rule">
    <Segmented label={`${label} budget`} value={fixed ? "fixed" : "percentage"}
      options={[{ value: "percentage", label: "Percentage" }, { value: "fixed", label: "Fixed amount" }]}
      onChange={kind => onChange(kind === "fixed"
        ? { allocationMode: "fixed_amount", capitalAmount: 100 }
        : { allocationMode: "custom_percentage", allocationPercent: 25 })} />
    <NumberField label={fixed ? `${label} amount per strategy` : `${label} percent per strategy`}
      value={fixed ? convertFromUsd(Number(rule.capitalAmount ?? 100)) : Number((percentage ?? 25).toFixed(4))}
      min={0.01} max={fixed ? undefined : 100} step={0.0001} suffix={fixed ? currencyCode : "%"}
      invalid={fixed ? !(Number(rule.capitalAmount) > 0) : !(Number(percentage) > 0 && Number(percentage) <= 100)}
      onChange={amount => onChange(fixed ? { allocationMode: "fixed_amount", capitalAmount: convertToUsd(amount) }
        : { allocationMode: "custom_percentage", allocationPercent: amount })} />
    {!fixed && <>
      <input className="budget-slider" type="range" aria-label={`${label} budget percentage`}
        min={1} max={100} step={1} value={percentage ?? 25}
        onChange={event => onChange({ allocationMode: "custom_percentage", allocationPercent: Number(event.target.value) })} />
      <div className="budget-presets" aria-label={`${label} percentage shortcuts`}>
        {[10, 25, 50, 100].map(amount => <button type="button" key={amount} aria-pressed={percentage === amount}
          onClick={() => onChange({ allocationMode: "custom_percentage", allocationPercent: amount })}>{amount}%</button>)}
      </div>
      {(rule.allocationMode === "one_third_balance" || rule.allocationMode === "equal_parts") && <p className="field-hint">Saved rule: {capitalRuleText(rule)}. Its exact calculation stays in place until you edit it.</p>}
    </>}
  </div>;
}

function LimitFields({ label, limit, onChange }: {
  label: string; limit: CapitalLimit | null | undefined; onChange: (limit: CapitalLimit | null) => void;
}) {
  const { currencyCode, convertFromUsd, convertToUsd } = useCurrency();
  return <div className="capital-rule">
    <Select label={label} value={limit?.mode ?? "none"} options={[
      { value: "none", label: "Up to all trading capital" }, { value: "percentage", label: "Percentage of trading capital" },
      { value: "fixed_amount", label: `Fixed ${currencyCode} amount` }
    ]} onChange={mode => {
      if (mode === "none") onChange(null);
      else if (mode === "percentage" || mode === "fixed_amount") onChange({ mode, value: 100 });
    }} />
    {limit && <NumberField label={`${label} amount`} min={0.01} max={limit.mode === "percentage" ? 100 : undefined} step={0.01}
      suffix={limit.mode === "percentage" ? "%" : currencyCode}
      value={limit.mode === "percentage" ? limit.value : convertFromUsd(limit.value)}
      invalid={!(limit.value > 0) || (limit.mode === "percentage" && limit.value > 100)}
      onChange={value => onChange({ ...limit, value: limit.mode === "percentage" ? value : convertToUsd(value) })} />}
  </div>;
}

export default function CapitalPolicyFields({ value, onChange, disabled = false, totalBalance = 0 }: {
  value: CapitalSettings; onChange: (settings: CapitalSettings) => void; disabled?: boolean; totalBalance?: number;
}) {
  const maximum = capitalStrategyLimit(value, totalBalance);
  // Keep the count stable when adding an asset override to a legacy policy.
  const changeBudget = (next: CapitalSettings) => onChange({ ...next, maximumConcurrentStrategies: value.maximumConcurrentStrategies ?? maximum });
  return <fieldset id="capital-budget-settings" tabIndex={-1} className="capital-policy-fields budget-fields" disabled={disabled}>
    <legend className="visually-hidden">Strategy budget settings</legend>
    <div className="budget-setting-heading"><span>01</span><div><h3>Size each strategy</h3><p>One budget covers all its legs. BTC and ETH share this default.</p></div></div>
    <RuleFields label="Default" rule={value} onChange={rule => changeBudget({ ...value, ...rule })} />
    <div className="budget-setting-heading"><span>02</span><div><h3>Set the boundaries</h3><p>Limit total allocated budgets and simultaneous runs.</p></div></div>
    <div className="budget-boundaries">
      <LimitFields label="Maximum total allocated" limit={value.overallLimit} onChange={overallLimit => onChange({ ...value, overallLimit })} />
      <NumberField label="Simultaneous strategies" value={maximum} min={1} max={100} step={1}
        hint={value.maximumConcurrentStrategies == null ? `Current automatic limit: ${maximum}. Editing a budget keeps this count.` : "A count limit. Funds and allocation caps still apply."}
        onChange={count => onChange({ ...value, maximumConcurrentStrategies: count })} />
    </div>
    <Select label="When the full budget is unavailable" value={value.allowSmallerEntries === false ? "wait" : "smaller"}
      options={[{ value: "wait", label: "Wait for the full budget" }, { value: "smaller", label: "Allow a smaller entry", hint: "Uses the available amount, subject to caps and minimum lot size" }]}
      onChange={kind => onChange({ ...value, allowSmallerEntries: kind === "smaller" })} />
    <details className="budget-advanced">
      <summary>Separate BTC & ETH rules<span>{Object.keys(value.assetOverrides ?? {}).length ? "Custom budgets active" : "Optional"}</span></summary>
      <div className="budget-advanced-body">
        {(["BTC", "ETH"] as const).map(asset => <section className="budget-asset-rule" key={asset} aria-label={`${asset} allocation`}>
          <label className="profile-checkbox"><input type="checkbox" checked={!!value.assetOverrides?.[asset]} onChange={event => {
            const overrides = { ...value.assetOverrides };
            if (event.target.checked) overrides[asset] = { allocationMode: value.allocationMode, capitalAmount: value.capitalAmount,
              allocationPercent: value.allocationPercent, divisionCount: value.divisionCount };
            else delete overrides[asset];
            changeBudget({ ...value, assetOverrides: overrides });
          }} />Use a different {asset} budget</label>
          {value.assetOverrides?.[asset] && <RuleFields label={asset} rule={value.assetOverrides[asset]} onChange={rule => changeBudget({ ...value, assetOverrides: { ...value.assetOverrides, [asset]: rule } })} />}
          <LimitFields label={`${asset} total allocation limit`} limit={value.assetLimits?.[asset]} onChange={limit => {
            const limits = { ...value.assetLimits };
            if (limit) limits[asset] = limit; else delete limits[asset];
            onChange({ ...value, assetLimits: limits });
          }} />
        </section>)}
      </div>
    </details>
    <p className="field-hint">Budgets are allocation limits, separate from stop-loss settings. Open runs keep their entry budgets.</p>
  </fieldset>;
}
