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
  userType?: "owner" | "user";
  phoneNumber?: string | null;
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
  exposureStatus?: "open" | "flat" | "unknown" | null;
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
  accountingBasis?: "allocated_exchange_fills";
  accountingComplete?: boolean;
  entryPremium?: string;
  exitPremium?: string;
  grossPnl?: string;
  commission?: string | null;
  realizedPnl?: string | null;
  slippageCost?: string;
  requestedLots?: string;
  filledLots?: string;
  closedLots?: string;
  fullyClosed?: boolean;
  settledAt?: string;
  closureReason?: "scheduled_exit" | "exchange_settlement" | "exchange_flat" | "exchange_fills";
  reconciledAt?: string;
  exchangeSettlementFillIds?: string[];
  bySymbol?: {
    symbol: string;
    entryPremium: string;
    exitPremium: string;
    commission: string | null;
    entryLots: string;
    exitLots: string;
    realizedPnl: string | null;
  }[];
};

/** Complete recorded history of one run, backing the run Information panel. */
export type RunDetail = StrategyRun & {
  updatedAt?: string | null;
  definition: Partial<StrategyDefinition>;
  savedStrategyId?: string | null;
  capitalSlot?: number | null;
  capitalBudget?: string | null;
  capitalPolicy: Record<string, unknown>;
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

export type CapitalAllocationMode =
  | "full_balance"
  | "half_balance"
  | "one_third_balance"
  | "one_quarter_balance"
  | "fixed_amount";

export type CapitalOverview = {
  success: boolean;
  settings: {
    allocationMode: CapitalAllocationMode;
    capitalAmount?: number | null;
  };
  wallet: {
    asset: "USD";
    totalBalance: number;
    availableBalance: number;
  };
  nominalBudgetPerStrategy: number;
  availableBudgetForNextStrategy: number;
  maximumConcurrentStrategies: number;
  occupiedAllocations: number;
  availableAllocations: number;
};

/** Shared built-in template or reusable definition in the account's private library. */
export type SavedStrategy = {
  id: string;
  isDefault: boolean;
  version: number;
  enabledForAi: boolean;
  name: string;
  definition: StrategyDefinition;
  createdAt: string;
  updatedAt: string;
};

/** Underlying asset whose agent produced a run or proposal. Older payloads omit it and mean BTC. */
export type AgentAsset = "BTC" | "ETH";

export const AGENT_ASSETS: readonly AgentAsset[] = ["BTC", "ETH"];

export type AutomationRun = {
  scope?: "shared" | "historical_account";
  asset?: AgentAsset;
  id: string;
  trigger: string;
  status: string;
  outcome?: string | null;
  scheduledFor: string;
  startedAt?: string | null;
  completedAt?: string | null;
  report?: string | null;
  charts: { id: string; label: string; altText: string; url: string }[];
  error?: string | null;
};

export type StrategyProposal = {
  id: string;
  asset?: AgentAsset;
  strategyName: string;
  strategyVersion: number;
  status: string;
  activationTime: string;
  expiresAt: string;
  confidence: number;
  reasoning: string;
};

export type AutomationOverview = {
  success: boolean;
  settings: {
    enabled: boolean;
    maximumConcurrentStrategies?: number | null;
  };
  enabledStrategies: number;
  totalStrategies: number;
  runs: AutomationRun[];
  upcomingRuns: Pick<AutomationRun, "id" | "asset" | "trigger" | "scheduledFor">[];
  proposals: StrategyProposal[];
};

/* ------------------------------------------------------------------
 * Software P&L and owner reporting. Money fields are USD decimal strings.
 * ------------------------------------------------------------------ */

export type ReportRange = "7d" | "30d" | "90d" | "1y" | "all";

/** Only `settled` runs count toward net P&L, wins and losses. */
export type AccountingState = "settled" | "open" | "scheduled" | "cancelled" | "incomplete" | "attention";

export type PnlSummary = {
  netRealizedPnl: string;
  grossGains: string;
  grossLosses: string;
  exchangeFees: string;
  wins: number;
  losses: number;
  breakEven: number;
  /** Share of settled runs that won, 0 to 1. Null when nothing has settled. */
  winRate: number | null;
  settledRuns: number;
  totalRuns: number;
  excludedRuns: number;
  states: Record<AccountingState, number>;
  deletedByUserRuns: number;
  lastCapturedAt: string | null;
};

export type PnlResponse = {
  success: boolean;
  scope: "personal";
  range: ReportRange;
  asOf: string;
  historyComplete: boolean;
  historyVerifiedAt: string | null;
  summary: PnlSummary;
};

export type TradeItem = {
  runId: string;
  name: string;
  asset: AgentAsset | null;
  status: string;
  accountingState: AccountingState;
  exclusionReason: string | null;
  createdAt: string;
  entryAt: string | null;
  exitAt: string | null;
  entryExecutedAt: string | null;
  exitExecutedAt: string | null;
  activityAt: string;
  realizedPnl: string | null;
  grossPnl: string | null;
  exchangeFees: string | null;
  capitalBudget: string | null;
  walletTotalAtEntry: string | null;
  walletAvailableAtEntry: string | null;
  /** Set on owner views when the user removed the run from their own history. */
  deletedByUserAt: string | null;
  capturedAt: string;
};

export type TradePage = { success: boolean; items: TradeItem[]; nextCursor: string | null };

export type OwnerAccount = {
  initialized: boolean;
  connectionStatus: string;
  accountName: string | null;
  email: string | null;
  deltaAccountId: string | null;
};

export type RecordedWallet = {
  totalBalance: string;
  availableBalance: string | null;
  observedAt: string;
  source: "strategy_entry" | "live_wallet";
};

export type OwnerProfile = {
  id: string;
  displayName: string | null;
  email: string | null;
  phoneNumber: string | null;
  avatarUrl: string | null;
  userType: "owner" | "user";
  registeredAt: string | null;
  isCurrentUser: boolean;
};

export type OwnerUserRow = OwnerProfile & {
  account: OwnerAccount;
  automationEnabled: boolean;
  performance: {
    netRealizedPnl: string;
    settledRuns: number;
    totalRuns: number;
    openRuns: number;
    attentionRuns: number;
    lastActivityAt: string | null;
  };
  lastRecordedWallet: RecordedWallet | null;
};

export type OwnerUsersResponse = {
  success: boolean;
  asOf: string;
  summary: {
    registeredUsers: number;
    connectedAccounts: number;
    automationEnabledAccounts: number;
    accountsWithAttentionRuns: number;
    automationWithoutConnection: number;
  };
  matching: number;
  offset: number;
  limit: number;
  items: OwnerUserRow[];
};

export type LiveWallet =
  | { state: "live"; totalBalance: string; availableBalance: string; observedAt: string }
  | { state: "unavailable"; reason: string; observedAt: null }
  | { state: "not_connected" };

export type CapitalObservation = {
  kind: "wallet" | "policy" | "run_allocation";
  source: "strategy_entry" | "live_wallet" | "capital_policy";
  observedAt: string;
  runId: string | null;
  totalBalance: string | null;
  availableBalance: string | null;
  allocationMode: string | null;
  capitalAmount: string | null;
  allocatedBudget: string | null;
};

export type OwnerUserDetail = {
  success: boolean;
  asOf: string;
  range: ReportRange;
  historyComplete: boolean;
  historyVerifiedAt: string | null;
  profile: OwnerProfile;
  account: OwnerAccount;
  automation: { enabled: boolean };
  capitalPolicy: { allocationMode: string | null; capitalAmount: string | null };
  /** Per-strategy budget under the saved policy. Null when the live wallet could not be read. */
  budgetPreview: {
    budgetPerStrategy: string;
    nextStrategyCanUse: string;
    maximumConcurrentStrategies: number;
  } | null;
  wallet: LiveWallet;
  capitalHistory: CapitalObservation[];
  performance: { ownerScope: PnlSummary; userScope: PnlSummary };
};

export type OwnerTradeDetail = {
  success: boolean;
  source: "live" | "archive";
  trade: TradeItem;
  firstCapturedAt: string;
  run: RunDetail;
};
