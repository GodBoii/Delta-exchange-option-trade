import type { CapitalAllocationMode, CapitalRule, CapitalSettings } from "@/lib/app-types";

/**
 * Per-strategy budget rules. Every rule is one sentence about what it does to the
 * balance, so the choice can be made from the list itself. Shared by the account
 * holder's capital panel and the owner's editor, so both offer the same rules.
 */
export const CAPITAL_MODE_OPTIONS: readonly { value: CapitalAllocationMode; label: string; hint: string }[] = [
  { value: "full_balance", label: "100% per strategy", hint: "One live strategy at a time" },
  { value: "half_balance", label: "50% per strategy", hint: "Up to two live strategies" },
  { value: "one_third_balance", label: "33.33% per strategy", hint: "Up to three live strategies" },
  { value: "one_quarter_balance", label: "25% per strategy", hint: "Up to four live strategies" },
  { value: "fixed_amount", label: "Fixed USD amount", hint: "A flat budget per strategy" },
  { value: "equal_parts", label: "Custom equal parts", hint: "Divide capital into 1–100 parts" },
  { value: "custom_percentage", label: "Custom percentage", hint: "Choose the percentage per strategy" }
];

export function isCapitalAllocationMode(value: string | null | undefined): value is CapitalAllocationMode {
  return CAPITAL_MODE_OPTIONS.some(option => option.value === value);
}

export function normalizeCapitalRule(rule: Omit<CapitalRule, "allocationMode"> & { allocationMode: string | null }): CapitalRule {
  return {
    allocationMode: isCapitalAllocationMode(rule.allocationMode) ? rule.allocationMode : "half_balance",
    capitalAmount: rule.allocationMode === "fixed_amount" ? Number(rule.capitalAmount ?? 100) : null,
    allocationPercent: rule.allocationMode === "custom_percentage" ? Number(rule.allocationPercent ?? 10) : null,
    divisionCount: rule.allocationMode === "equal_parts" ? rule.divisionCount ?? 10 : null
  };
}

export function normalizeCapitalSettings(settings: Omit<CapitalSettings, "allocationMode"> & { allocationMode: string | null }): CapitalSettings {
  const overrides: CapitalSettings["assetOverrides"] = {};
  const limits: CapitalSettings["assetLimits"] = {};
  for (const asset of ["BTC", "ETH"] as const) {
    const rule = settings.assetOverrides?.[asset];
    if (rule) overrides[asset] = normalizeCapitalRule(rule);
    const limit = settings.assetLimits?.[asset];
    if (limit) limits[asset] = limit;
  }
  return {
    ...normalizeCapitalRule(settings), assetOverrides: overrides,
    overallLimit: settings.overallLimit ?? null, assetLimits: limits,
    maximumConcurrentStrategies: settings.maximumConcurrentStrategies ?? null
  };
}

export function capitalSettingsError(settings: CapitalSettings): string | null {
  const rules = [{ label: "Default", rule: settings },
    ...Object.entries(settings.assetOverrides ?? {}).map(([label, rule]) => ({ label, rule }))];
  for (const { label, rule } of rules) {
    const amount = Number(rule.capitalAmount), percentage = Number(rule.allocationPercent), parts = rule.divisionCount;
    if (rule.allocationMode === "fixed_amount" && (!Number.isFinite(amount) || amount <= 0))
      return `${label} amount must be greater than zero.`;
    if (rule.allocationMode === "custom_percentage" && (!Number.isFinite(percentage) || percentage <= 0 || percentage > 100))
      return `${label} percentage must be greater than zero and at most 100.`;
    if (rule.allocationMode === "equal_parts" && (parts == null || !Number.isInteger(parts) || parts < 1 || parts > 100))
      return `${label} division count must be a whole number from 1 to 100.`;
  }
  for (const limit of [settings.overallLimit, ...Object.values(settings.assetLimits ?? {})]) {
    if (limit && (!Number.isFinite(limit.value) || limit.value <= 0 || (limit.mode === "percentage" && limit.value > 100)))
      return "Capital caps must be positive. Percentage caps cannot exceed 100%.";
  }
  const count = settings.maximumConcurrentStrategies;
  if (count != null && (!Number.isInteger(count) || count < 1 || count > 100))
    return "The concurrent strategy limit must be a whole number from 1 to 100.";
  return null;
}

export function capitalRuleText(rule: CapitalRule): string {
  if (rule.allocationMode === "equal_parts") return `${rule.divisionCount} equal parts`;
  if (rule.allocationMode === "custom_percentage") return `${rule.allocationPercent}% per strategy`;
  return CAPITAL_MODE_OPTIONS.find(option => option.value === rule.allocationMode)?.label ?? rule.allocationMode;
}
