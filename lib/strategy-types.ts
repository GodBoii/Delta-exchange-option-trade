export type StrategyLeg = {
  id: string;
  lots: number;
  position: "buy" | "sell";
  optionType: "call" | "put";
  expiry: string;
  strikeMode: "atm" | "itm" | "otm" | "exact";
  strikeSteps: number;
  exactStrike?: number;
  orderType: "market_order" | "limit_order";
  limitPrice?: string;
  targetProfit?: number;
  stopLoss?: number;
  trailStop?: number;
  reentryOnTarget: number;
  reentryOnStop: number;
};

export type StrategyDefinition = {
  name: string;
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
  squareOff: "partial" | "complete";
  riskMode: "legwise" | "combined_premium";
  combinedStopLossPercent?: number;
  emergencyStopLossPercent?: number;
  trailToBreakEven: boolean;
  breakEvenScope: "all_legs" | "stop_loss_legs";
  overallTarget?: number;
  overallStopLoss?: number;
  legs: StrategyLeg[];
  acknowledgement: true;
};
