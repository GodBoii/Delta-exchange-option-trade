/**
 * Portfolio maths shared by the live stream and the REST fallback.
 *
 * Delta records arrive as loosely typed JSON, from `/api/account/overview` or
 * from the `/ws/portfolio` stream (a trimmed subset with the same keys), so every
 * field is read defensively here and nowhere else.
 *
 * Unrealised P&L is recomputed from the live mark instead of trusting the
 * record's `unrealized_pnl`: for short options Delta reports the unsigned
 * premium value there, not the profit or loss.
 */

import type { DeltaRecord } from "@/lib/app-types";
import { toNumber } from "@/lib/format";

export type PriceMap = Readonly<Record<string, number>>;
export type LivePrices = { readonly marks: PriceMap; readonly indices: PriceMap };
export const NO_PRICES: LivePrices = { marks: {}, indices: {} };

export type ProtectiveOrder = {
  kind: "stop_loss" | "take_profit";
  orderId: string | null;
  trigger: number | null;
  /** `null` means the triggered order executes at market. */
  limit: number | null;
  method: string | null;
  trail: number | null;
};

export type PositionView = {
  key: string;
  productId: number | null;
  symbol: string;
  /** Signed contract count: positive long, negative short. */
  size: number;
  side: "long" | "short";
  contractType: string | null;
  isOption: boolean;
  underlying: string | null;
  strike: number | null;
  settlementTime: string | null;
  contractValue: number | null;
  /** |size| × contract value, in the contract's unit (BTC for BTC options). */
  units: number | null;
  entry: number | null;
  mark: number | null;
  /** Whether `mark` came from the live feed or the last account snapshot. */
  markSource: "live" | "snapshot" | null;
  index: number | null;
  indexSymbol: string | null;
  /** USD value of `units` at the index price. */
  notional: number | null;
  /** Premium received (short option) or paid (long option); entry value for futures. */
  entryValue: number | null;
  unrealizedPnl: number | null;
  /** Unrealised P&L as a share of `entryValue`. */
  unrealizedPercent: number | null;
  realizedCashflow: number | null;
  realizedPnl: number | null;
  realizedFunding: number | null;
  margin: number | null;
  marginMode: string | null;
  liquidation: number | null;
  /** Distance from mark to the liquidation trigger, as a share of mark. */
  liquidationDistance: number | null;
  effectiveLeverage: number | null;
  commission: number | null;
  stopLoss: ProtectiveOrder | null;
  takeProfit: ProtectiveOrder | null;
  record: DeltaRecord;
};

export function isRecord(value: unknown): value is DeltaRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** First key that holds a real value; Delta sends `null` and `""` for absent fields. */
export function readValue(record: DeltaRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

export function readNumber(record: DeltaRecord, ...keys: string[]): number | null {
  return toNumber(readValue(record, ...keys));
}

export function readText(record: DeltaRecord, ...keys: string[]): string | null {
  const value = readValue(record, ...keys);
  return value === undefined ? null : String(value);
}

function readRecord(record: DeltaRecord | null, key: string): DeltaRecord | null {
  const value = record?.[key];
  return isRecord(value) ? value : null;
}

const CLOSED_ORDER_STATES = new Set(["closed", "cancelled"]);
const STOP_KINDS = { stop_loss_order: "stop_loss", take_profit_order: "take_profit" } as const;

function stopKind(order: DeltaRecord): ProtectiveOrder["kind"] | null {
  const type = readText(order, "stop_order_type");
  return type === "stop_loss_order" || type === "take_profit_order" ? STOP_KINDS[type] : null;
}

/** Stop-loss and take-profit orders, including the bracket legs Delta attaches to a position. */
export function isProtectiveOrder(order: DeltaRecord) {
  return stopKind(order) !== null;
}

function protectiveOrders(orders: readonly DeltaRecord[], productId: number | null, size: number) {
  const closingSide = size > 0 ? "sell" : "buy";
  const found: { stopLoss: ProtectiveOrder | null; takeProfit: ProtectiveOrder | null } = {
    stopLoss: null, takeProfit: null
  };
  if (productId === null || size === 0) return found;
  for (const order of orders) {
    const kind = stopKind(order);
    if (!kind || readNumber(order, "product_id") !== productId) continue;
    if (readText(order, "side") !== closingSide || CLOSED_ORDER_STATES.has(readText(order, "state") ?? "")) continue;
    const view: ProtectiveOrder = {
      kind,
      orderId: readText(order, "id", "order_id"),
      trigger: readNumber(order, "stop_price"),
      limit: readNumber(order, "limit_price"),
      method: readText(order, "stop_trigger_method"),
      trail: readNumber(order, "trail_amount")
    };
    if (kind === "stop_loss") found.stopLoss ??= view;
    else found.takeProfit ??= view;
  }
  return found;
}

export function positionView(
  record: DeltaRecord, orders: readonly DeltaRecord[], prices: LivePrices, fallbackKey: number
): PositionView {
  const product = readRecord(record, "product");
  const productId = readNumber(record, "product_id");
  const symbol = readText(record, "product_symbol", "symbol") ?? `Product ${productId ?? fallbackKey}`;
  const size = readNumber(record, "size") ?? 0;
  const contractType = product ? readText(product, "contract_type") : null;
  const contractValue = product ? readNumber(product, "contract_value") : readNumber(record, "contract_value");
  const notionalType = product ? readText(product, "notional_type") : null;
  const indexSymbol = readText(readRecord(product, "spot_index") ?? {}, "symbol");
  const entry = readNumber(record, "entry_price");

  const liveMark = prices.marks[symbol];
  const snapshotMark = readNumber(record, "mark_price");
  const mark = liveMark ?? snapshotMark;
  const markSource = liveMark !== undefined ? "live" : snapshotMark !== null ? "snapshot" : null;
  const index = indexSymbol ? prices.indices[indexSymbol] ?? null : null;

  const units = contractValue !== null ? Math.abs(size) * contractValue : null;
  const entryValue = entry !== null && units !== null ? entry * units : null;
  // Linear (vanilla) contracts settle in the quoting currency, so P&L is the
  // price move times the underlying quantity. Inverse contracts would need a
  // different formula and a non-USD currency, so they stay unpriced here.
  const linear = notionalType === "vanilla";
  const unrealizedPnl = linear && mark !== null && entry !== null && contractValue !== null
    ? (mark - entry) * size * contractValue
    : null;
  const unrealizedPercent = unrealizedPnl !== null && entryValue ? (unrealizedPnl / entryValue) * 100 : null;
  const notional = units !== null && index !== null ? units * index : null;
  const margin = readNumber(record, "margin");
  // Matches Delta's "Effective Lev.": exposure over the equity the position holds.
  const equity = margin !== null ? margin + (unrealizedPnl ?? 0) : null;
  const effectiveLeverage = notional !== null && equity !== null && equity > 0 ? notional / equity : null;
  const liquidation = readNumber(record, "liquidation_price");
  const liquidationDistance = liquidation !== null && mark !== null && mark > 0
    ? (Math.abs(mark - liquidation) / mark) * 100
    : null;

  return {
    key: productId !== null ? String(productId) : `${symbol}-${fallbackKey}`,
    productId,
    symbol,
    size,
    side: size > 0 ? "long" : "short",
    contractType,
    isOption: contractType === "call_options" || contractType === "put_options",
    underlying: readText(readRecord(product, "underlying_asset") ?? {}, "symbol"),
    strike: product ? readNumber(product, "strike_price") : null,
    settlementTime: product ? readText(product, "settlement_time") : null,
    contractValue,
    units,
    entry,
    mark,
    markSource,
    index,
    indexSymbol,
    notional,
    entryValue,
    unrealizedPnl,
    unrealizedPercent,
    realizedCashflow: readNumber(record, "realized_cashflow"),
    realizedPnl: readNumber(record, "realized_pnl"),
    realizedFunding: readNumber(record, "realized_funding"),
    margin,
    marginMode: readText(record, "margin_mode"),
    liquidation,
    liquidationDistance,
    effectiveLeverage,
    commission: readNumber(record, "commission"),
    ...protectiveOrders(orders, productId, size),
    record
  };
}

export function positionViews(
  positions: readonly DeltaRecord[], orders: readonly DeltaRecord[], prices: LivePrices
): PositionView[] {
  return positions
    .filter(record => (readNumber(record, "size") ?? 0) !== 0)
    .map((record, index) => positionView(record, orders, prices, index));
}

/** Sum of the figures that can be priced; `null` when none can. */
export function totalUnrealized(views: readonly PositionView[]): number | null {
  const priced = views.filter(view => view.unrealizedPnl !== null);
  return priced.length ? priced.reduce((sum, view) => sum + (view.unrealizedPnl ?? 0), 0) : null;
}

export type OrderDescription = {
  typeLabel: string;
  protective: boolean;
  trigger: number | null;
  triggerMethod: string | null;
  limit: number | null;
  average: number | null;
};

const TRIGGER_METHODS: Record<string, string> = {
  mark_price: "mark",
  last_traded_price: "last price",
  spot_price: "index"
};

export function triggerMethodLabel(method: string | null) {
  return method ? TRIGGER_METHODS[method] ?? method.replaceAll("_", " ") : null;
}

export function describeOrder(order: DeltaRecord): OrderDescription {
  const kind = stopKind(order);
  const limit = readNumber(order, "limit_price");
  const execution = limit !== null ? "limit" : "market";
  const typeLabel = kind === "stop_loss" ? `Stop loss · ${execution}`
    : kind === "take_profit" ? `Take profit · ${execution}`
      : (readText(order, "order_type") ?? "order").replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
  return {
    typeLabel,
    protective: kind !== null,
    trigger: readNumber(order, "stop_price"),
    triggerMethod: readText(order, "stop_trigger_method"),
    limit,
    average: readNumber(order, "average_fill_price")
  };
}

/* ------------------------------------------------------------------ *
 * Stream protocol
 * ------------------------------------------------------------------ */

export type StreamMessage =
  | { type: "status"; private: "live" | "syncing" }
  | { type: "state"; positions: DeltaRecord[]; orders: DeltaRecord[]; balances: DeltaRecord[]; at: number }
  | { type: "prices"; marks: PriceMap; indices: PriceMap; at: number }
  | { type: "ping" };

function recordList(value: unknown): DeltaRecord[] | null {
  return Array.isArray(value) && value.every(isRecord) ? value : null;
}

function priceMap(value: unknown): PriceMap | null {
  if (!isRecord(value)) return null;
  const prices: Record<string, number> = {};
  for (const [symbol, raw] of Object.entries(value)) {
    const price = toNumber(raw);
    if (price !== null && price > 0) prices[symbol] = price;
  }
  return prices;
}

/** Validates one server frame; anything unexpected is dropped rather than trusted. */
export function parseStreamMessage(text: string): StreamMessage | null {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(value)) return null;
  const at = toNumber(value.at) ?? Date.now();
  switch (value.type) {
    case "status":
      return value.private === "live" || value.private === "syncing" ? { type: "status", private: value.private } : null;
    case "state": {
      const positions = recordList(value.positions);
      const orders = recordList(value.orders);
      const balances = recordList(value.balances);
      return positions && orders && balances ? { type: "state", positions, orders, balances, at } : null;
    }
    case "prices": {
      const marks = priceMap(value.marks);
      const indices = priceMap(value.indices);
      return marks && indices ? { type: "prices", marks, indices, at } : null;
    }
    case "ping":
      return { type: "ping" };
    default:
      return null;
  }
}
