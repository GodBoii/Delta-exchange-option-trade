import { z } from "zod";
import type { DeltaTicker } from "@/lib/delta";
import { AppError } from "@/lib/http";

export const legSchema = z.object({
  id: z.string().min(1).max(40),
  lots: z.number().int().min(1).max(100_000),
  position: z.enum(["buy", "sell"]),
  optionType: z.enum(["call", "put"]),
  expiry: z.string().date(),
  strikeMode: z.enum(["atm", "itm", "otm", "exact"]),
  strikeSteps: z.number().int().min(0).max(100).default(0),
  exactStrike: z.number().positive().optional(),
  orderType: z.enum(["market_order", "limit_order"]).default("market_order"),
  limitPrice: z.string().regex(/^\d+(\.\d+)?$/).optional(),
  targetProfit: z.number().positive().optional(),
  stopLoss: z.number().positive().optional(),
  trailStop: z.number().positive().optional(),
  reentryOnTarget: z.number().int().min(0).max(10).default(0),
  reentryOnStop: z.number().int().min(0).max(10).default(0)
}).superRefine((leg, context) => {
  if (leg.strikeMode === "exact" && !leg.exactStrike) context.addIssue({ code: "custom", path: ["exactStrike"], message: "Exact strike is required" });
  if (leg.orderType === "limit_order" && !leg.limitPrice) context.addIssue({ code: "custom", path: ["limitPrice"], message: "Limit price is required" });
});

export const strategySchema = z.object({
  name: z.string().trim().min(2).max(80),
  instrument: z.object({
    index: z.enum(["BTCUSD", "ETHUSD"]),
    underlying: z.enum(["BTC", "ETH"]),
    underlyingFrom: z.enum(["cash", "futures"])
  }),
  entry: z.object({
    strategyType: z.enum(["intraday", "btst", "positional"]),
    entryAt: z.string().datetime(),
    exitAt: z.string().datetime()
  }),
  squareOff: z.enum(["partial", "complete"]).default("complete"),
  trailToBreakEven: z.boolean().default(false),
  breakEvenScope: z.enum(["all_legs", "stop_loss_legs"]).default("all_legs"),
  overallTarget: z.number().positive().optional(),
  overallStopLoss: z.number().positive().optional(),
  legs: z.array(legSchema).min(1).max(12),
  acknowledgement: z.literal(true)
}).superRefine((strategy, context) => {
  if (new Date(strategy.entry.exitAt) <= new Date(strategy.entry.entryAt)) {
    context.addIssue({ code: "custom", path: ["entry", "exitAt"], message: "Exit must be after entry" });
  }
  for (const [index, leg] of strategy.legs.entries()) {
    if (leg.position === "sell" && !leg.stopLoss) {
      context.addIssue({ code: "custom", path: ["legs", index, "stopLoss"], message: "Short option legs require a stop loss" });
    }
  }
});

export type StrategyDefinition = z.infer<typeof strategySchema>;
export type StrategyLeg = z.infer<typeof legSchema>;

export type ResolvedLeg = StrategyLeg & {
  productId: number;
  productSymbol: string;
  strike: number;
  markPrice: string | null;
};

export function deltaExpiry(date: string) {
  const [year, month, day] = date.split("-");
  return `${day}-${month}-${year}`;
}

export function resolveLeg(leg: StrategyLeg, chain: DeltaTicker[]): ResolvedLeg {
  const contractType = leg.optionType === "call" ? "call_options" : "put_options";
  const candidates = chain
    .filter((item) => item.contract_type === contractType && item.strike_price)
    .map((item) => ({ item, strike: Number(item.strike_price) }))
    .filter(({ strike }) => Number.isFinite(strike))
    .sort((a, b) => a.strike - b.strike);
  if (!candidates.length) throw new AppError(422, `No live ${leg.optionType} options found for ${leg.expiry}`, "option_chain_empty");
  const spot = Number(candidates.find(({ item }) => item.spot_price)?.item.spot_price);
  if (!Number.isFinite(spot)) throw new AppError(422, "Option chain did not include a spot price", "spot_price_missing");
  let index: number;
  if (leg.strikeMode === "exact") {
    index = candidates.findIndex(({ strike }) => strike === leg.exactStrike);
    if (index < 0) throw new AppError(422, `Strike ${leg.exactStrike} is not listed`, "strike_not_found");
  } else {
    const atmIndex = candidates.reduce((best, candidate, current) =>
      Math.abs(candidate.strike - spot) < Math.abs(candidates[best].strike - spot) ? current : best, 0);
    const direction = leg.strikeMode === "atm" ? 0
      : leg.optionType === "call"
        ? (leg.strikeMode === "otm" ? 1 : -1)
        : (leg.strikeMode === "otm" ? -1 : 1);
    index = Math.max(0, Math.min(candidates.length - 1, atmIndex + direction * leg.strikeSteps));
  }
  const selected = candidates[index].item;
  return {
    ...leg,
    productId: selected.product_id,
    productSymbol: selected.symbol,
    strike: candidates[index].strike,
    markPrice: selected.mark_price ?? null
  };
}
