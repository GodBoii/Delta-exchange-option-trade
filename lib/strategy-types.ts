export type StrategyCategory =
  | "premium_buying"
  | "premium_selling"
  | "defined_risk_premium_selling";

export type MarketOutlook =
  | "bullish"
  | "bearish"
  | "large_move_unknown_direction"
  | "very_large_move_unknown_direction"
  | "sideways"
  | "wide_sideways"
  | "tight_sideways";

export type ExpiryPolicy = "same_day" | "next_day" | "7_day" | "30_day";
export type HoldingMode = "intraday" | "hold_to_expiry";
export type RiskBasis = "net_debit" | "net_credit" | "defined_max_loss";

export type StrategyLeg = {
  id: string;
  /** Resolved execution quantity. Ignored while lotsMode is auto. */
  lots: number;
  position: "buy" | "sell";
  optionType: "call" | "put";
  /** Fallback date for legacy runs and previews. New definitions use expiryPolicy. */
  expiry: string;
  strikeMode: "atm" | "itm" | "otm" | "exact";
  strikeSteps: number;
  exactStrike?: number;
  orderType: "market_order" | "limit_order";
  limitPrice?: string;
  role?: string;
  targetProfit?: number;
  stopLoss?: number;
  trailStop?: number;
  reentryOnTarget: number;
  reentryOnStop: number;
};

export type StrategyDefinition = {
  schemaVersion: 2;
  version: number;
  name: string;
  description: string;
  category: StrategyCategory;
  marketOutlook: MarketOutlook;
  enabledForAi: boolean;
  instrument: {
    index: "BTCUSD" | "ETHUSD";
    underlying: "BTC" | "ETH";
    underlyingFrom: "cash" | "futures";
  };
  entry: {
    strategyType: "intraday" | "btst" | "positional";
    entryAt: string;
    exitAt: string;
  };
  holdingMode: HoldingMode;
  expiryPolicy: ExpiryPolicy;
  exitMinutesBeforeExpiry: number;
  sameExpiryRequired: boolean;
  squareOff: "partial" | "complete";
  riskMode: "legwise" | "combined_premium" | "strategy_level";
  riskBasis: RiskBasis;
  stopLossPercent: number;
  takeProfitPercent: number;
  combinedStopLossPercent?: number;
  emergencyStopLossPercent?: number;
  emergencyExitEnabled: boolean;
  trailToBreakEven: boolean;
  breakEvenScope: "all_legs" | "stop_loss_legs";
  overallTarget?: number;
  overallStopLoss?: number;
  allocationMode: "one_of_three_account_slots";
  lotsMode: "auto" | "manual";
  maximumLots?: number;
  equalLotsRequired: boolean;
  legs: StrategyLeg[];
  acknowledgement: true;
};
