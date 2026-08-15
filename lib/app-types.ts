import type { StrategyDefinition } from "@/lib/strategy-types";

/** Delta Exchange connection as reported by the trading backend. */
export type Account = {
  id: string;
  accountName?: string | null;
  email?: string | null;
  environment: "production";
};

/** Workspace account from Supabase Auth. Distinct from the Delta account. */
export type AppUser = {
  id: string;
  email?: string | null;
  displayName?: string | null;
  avatarUrl?: string | null;
};

export type SessionResponse = {
  success: boolean;
  authenticated: boolean;
  connected: boolean;
  user: AppUser | null;
  account: Account | null;
  message?: string;
  error?: string;
};

/** One immutable scheduled execution of a saved definition. */
export type StrategyRun = {
  id: string;
  name: string;
  status: string;
  entryAt: string;
  exitAt: string;
  entryExecutedAt?: string | null;
  lastError?: string | null;
  createdAt: string;
};

export type RiskStrategy = {
  id: string;
  name: string;
  status: string;
  riskState: Record<string, unknown>;
  monitoredAt?: string | null;
  triggeredAt?: string | null;
};

/**
 * Delta wallet, order, and position payloads are passed through unmodified by
 * the backend, so they stay loosely typed here and are read through defensive
 * field accessors at the point of display.
 */
export type DeltaRecord = Record<string, unknown>;

export type AccountOverview = {
  balances: DeltaRecord[];
  orders: DeltaRecord[];
  positions: DeltaRecord[];
  riskStrategies: RiskStrategy[];
};

/** Reusable definition in the private Supabase library. */
export type SavedStrategy = {
  id: string;
  name: string;
  definition: StrategyDefinition;
  createdAt: string;
  updatedAt: string;
};
