"use client";

import { useCurrency } from "@/app/components/currency";
import { NumberField, Select, type SelectOption } from "@/app/components/ui";
import type { CapitalLimit, CapitalRule, CapitalSettings } from "@/lib/app-types";
import { CAPITAL_MODE_OPTIONS, isCapitalAllocationMode, normalizeCapitalRule } from "@/lib/capital";

function RuleFields({ label, rule, onChange, inherit = false }: {
  label: string; rule: CapitalRule | undefined; onChange: (rule: CapitalRule | undefined) => void; inherit?: boolean;
}) {
  const { currencyCode, convertFromUsd, convertToUsd } = useCurrency();
  const options: SelectOption[] = CAPITAL_MODE_OPTIONS.map(option => option.value === "fixed_amount"
    ? { ...option, label: `Fixed ${currencyCode} amount` } : { ...option });
  if (inherit) options.unshift({ value: "inherit", label: "Use default budget", hint: "Follow the account default" });
  return <div className="capital-rule">
    <Select label={`${label} budget per strategy`} value={rule?.allocationMode ?? "inherit"} options={options}
      onChange={value => {
        if (value === "inherit") onChange(undefined);
        else if (isCapitalAllocationMode(value)) onChange(normalizeCapitalRule({
          allocationMode: value, capitalAmount: rule?.capitalAmount ?? 100,
          allocationPercent: rule?.allocationPercent ?? 10, divisionCount: rule?.divisionCount ?? 10
        }));
      }} />
    {rule?.allocationMode === "fixed_amount" && <NumberField label={`${label} amount`} min={0.01} step={0.01}
      value={convertFromUsd(Number(rule.capitalAmount ?? 100))} suffix={currencyCode}
      invalid={!(Number(rule.capitalAmount) > 0)} onChange={value => onChange({ ...rule, capitalAmount: convertToUsd(value) })} />}
    {rule?.allocationMode === "custom_percentage" && <NumberField label={`${label} percentage`} min={0.01} max={100} step={0.01}
      value={Number(rule.allocationPercent ?? 10)} suffix="%"
      invalid={!(Number(rule.allocationPercent) > 0 && Number(rule.allocationPercent) <= 100)}
      onChange={value => onChange({ ...rule, allocationPercent: value })} />}
    {rule?.allocationMode === "equal_parts" && <NumberField label={`${label} equal parts`} min={1} max={100} step={1}
      value={rule.divisionCount ?? 10} invalid={!Number.isInteger(rule.divisionCount) || !(Number(rule.divisionCount) >= 1 && Number(rule.divisionCount) <= 100)}
      onChange={value => onChange({ ...rule, divisionCount: value })} />}
  </div>;
}

function LimitFields({ label, limit, onChange }: {
  label: string; limit: CapitalLimit | null | undefined; onChange: (limit: CapitalLimit | null) => void;
}) {
  const { currencyCode, convertFromUsd, convertToUsd } = useCurrency();
  return <div className="capital-rule">
    <Select label={`${label} total capital cap`} value={limit?.mode ?? "none"} options={[
      { value: "none", label: "No additional cap" }, { value: "percentage", label: "Percentage of wallet" },
      { value: "fixed_amount", label: `Fixed ${currencyCode} cap` }
    ]} onChange={mode => {
      if (mode === "none") onChange(null);
      else if (mode === "percentage" || mode === "fixed_amount") onChange({ mode, value: 100 });
    }} />
    {limit && <NumberField label={`${label} cap`} min={0.01} max={limit.mode === "percentage" ? 100 : undefined} step={0.01}
      suffix={limit.mode === "percentage" ? "%" : currencyCode}
      value={limit.mode === "percentage" ? limit.value : convertFromUsd(limit.value)}
      invalid={!(limit.value > 0) || (limit.mode === "percentage" && limit.value > 100)}
      onChange={value => onChange({ ...limit, value: limit.mode === "percentage" ? value : convertToUsd(value) })} />}
  </div>;
}

/** Both the account holder and the owner edit the same allocation contract. */
export default function CapitalPolicyFields({ value, onChange, disabled = false }: {
  value: CapitalSettings; onChange: (settings: CapitalSettings) => void; disabled?: boolean;
}) {
  return <fieldset className="capital-policy-fields" disabled={disabled}>
    <legend className="visually-hidden">Capital allocation settings</legend>
    <p className="field-hint">Percentages use the total USD wallet balance. Each strategy includes all its legs.
      BTC and ETH share the wallet. Open runs keep their entry budgets.</p>
    <RuleFields label="Default" rule={value} onChange={rule => { if (rule) onChange({ ...value, ...rule }); }} />
    <div className="capital-asset-fields">
      {(["BTC", "ETH"] as const).map(asset => <section className="capital-rule capital-asset" key={asset} aria-label={`${asset} allocation`}>
        <header className="capital-asset-heading">
          <span aria-hidden="true">{asset === "BTC" ? "₿" : "Ξ"}</span>
          <h3>{asset}<small>{asset === "BTC" ? "Bitcoin" : "Ethereum"}</small></h3>
        </header>
        <RuleFields label={asset} rule={value.assetOverrides?.[asset]} inherit onChange={rule => {
          const overrides = { ...value.assetOverrides };
          if (rule) overrides[asset] = rule;
          else delete overrides[asset];
          onChange({ ...value, assetOverrides: overrides });
        }} />
        <LimitFields label={asset} limit={value.assetLimits?.[asset]} onChange={limit => {
          const limits = { ...value.assetLimits };
          if (limit) limits[asset] = limit;
          else delete limits[asset];
          onChange({ ...value, assetLimits: limits });
        }} />
      </section>)}
    </div>
    <LimitFields label="Overall" limit={value.overallLimit} onChange={overallLimit => onChange({ ...value, overallLimit })} />
    <p className="field-hint">Caps limit the sum of reserved budgets across open strategies and logins sharing this wallet.
      Asset budgets are per strategy. Caps do not reserve funds for future trades.</p>
    <NumberField label="Maximum concurrent strategies" value={value.maximumConcurrentStrategies ?? 0} min={0} max={100} step={1}
      hint="0 uses the default rule's limit, or 100 when asset budgets differ. Capital caps still apply."
      onChange={count => onChange({ ...value, maximumConcurrentStrategies: count === 0 ? null : count })} />
  </fieldset>;
}
