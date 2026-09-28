import type { CapitalAllocationMode } from "@/lib/app-types";

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
  { value: "fixed_amount", label: "Fixed USD amount", hint: "A flat budget per strategy" }
];

export function isCapitalAllocationMode(value: string | null | undefined): value is CapitalAllocationMode {
  return CAPITAL_MODE_OPTIONS.some(option => option.value === value);
}
