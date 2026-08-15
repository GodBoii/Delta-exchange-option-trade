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
  exitExecutedAt?: string | null;
  lastError?: string | null;
  createdAt: string;
};

/** One order Delta accepted for a run, entry or exit, with its audit trail. */
export type RunOrder = {
  id: string;
  kind: "entry" | "exit";
  legId?: string | null;
  deltaOrderId?: string | null;
  clientOrderId?: string | null;
  productId?: number | null;
  productSymbol?: string | null;
  side?: "buy" | "sell" | null;
  /** Requested lots. */
  size: string;
  filledSize: string;
  averageFillPrice?: string | null;
  /** Mark price observed before submission: the slippage baseline. */
  referencePrice?: string | null;
  /** Positive is adverse, in quote currency per unit. */
  slippage?: string | null;
  slippagePercent?: string | null;
  contractValue?: string | null;
  orderType?: string | null;
  limitPrice?: string | null;
  commission: string;
  state?: string | null;
  createdAt?: string | null;
  response: Record<string, unknown>;
};

export type RunExecution = {
  id: string;
  kind: "entry" | "exit";
  status: string;
  error?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
};

/** Money view of a run, recomputed from the recorded orders on every read. */
export type RunSettlement = {
  entryPremium?: string;
  exitPremium?: string;
  grossPnl?: string;
  commission?: string;
  realizedPnl?: string;
  slippageCost?: string;
  requestedLots?: string;
  filledLots?: string;
  closedLots?: string;
  fullyClosed?: boolean;
  settledAt?: string;
  bySymbol?: {
    symbol: string;
    entryPremium: string;
    exitPremium: string;
    commission: string;
    entryLots: string;
    exitLots: string;
    realizedPnl: string;
  }[];
};

/** Complete recorded history of one run, backing the run Information panel. */
export type RunDetail = StrategyRun & {
  updatedAt?: string | null;
  definition: Partial<StrategyDefinition>;
  savedStrategyId?: string | null;
  riskState: Record<string, unknown>;
  riskMonitoredAt?: string | null;
  combinedStopTriggeredAt?: string | null;
  settlement: RunSettlement;
  executions: RunExecution[];
  orders: RunOrder[];
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
