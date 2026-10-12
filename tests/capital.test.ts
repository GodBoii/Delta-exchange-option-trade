import { describe, expect, it } from "vitest";
import { capitalRuleText, capitalSettingsError, normalizeCapitalSettings } from "@/lib/capital";

describe("capital settings shared by user and owner editors", () => {
  it("normalizes API decimal strings without changing asset overrides", () => {
    const policy = normalizeCapitalSettings({ allocationMode: "fixed_amount", capitalAmount: "250.0",
      assetOverrides: { ETH: { allocationMode: "custom_percentage", allocationPercent: "10.0" } },
      overallLimit: { mode: "percentage", value: 60 } });
    expect(policy.capitalAmount).toBe(250);
    expect(policy.assetOverrides?.ETH?.allocationPercent).toBe(10);
    expect(capitalSettingsError(policy)).toBeNull();
  });

  it("rejects fractional parts, invalid percentages and invalid caps", () => {
    expect(capitalSettingsError({ allocationMode: "equal_parts", divisionCount: 2.5 })).toMatch(/whole number/);
    expect(capitalSettingsError({ allocationMode: "custom_percentage", allocationPercent: 101 })).toMatch(/at most 100/);
    expect(capitalSettingsError({ allocationMode: "half_balance", assetOverrides: {
      ETH: { allocationMode: "fixed_amount", capitalAmount: 0 }
    } })).toMatch(/ETH amount/);
    expect(capitalSettingsError({ allocationMode: "half_balance", overallLimit: { mode: "percentage", value: 101 } })).toMatch(/caps/);
    expect(capitalSettingsError({ allocationMode: "half_balance", maximumConcurrentStrategies: 101 })).toMatch(/concurrent/);
  });

  it("keeps legacy settings and formats extended rules", () => {
    expect(normalizeCapitalSettings({ allocationMode: "one_quarter_balance" }).assetOverrides).toEqual({});
    expect(capitalRuleText({ allocationMode: "custom_percentage", allocationPercent: 20 })).toBe("20% per strategy");
    expect(capitalRuleText({ allocationMode: "equal_parts", divisionCount: 20 })).toBe("20 equal parts");
  });
});
