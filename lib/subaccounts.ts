export type ManagementMode = "main" | "automatic" | "manual";
export type Participation = "disabled" | "manual" | "automatic";
export type ManagedAccount = {
  id: string;
  name: string;
  parentId: string;
  marginMode: string | null;
  present: boolean;
  connected: boolean;
  busy: boolean;
  participation: Participation;
  strategyId: string | null;
  strategyName: string | null;
  budget: string | null;
};
export type Transfer = {
  id: string;
  source_id: string;
  target_id: string;
  amount: string;
  state: "prepared" | "submitting" | "confirmed" | "unknown" | "failed";
  error: string | null;
  created_at: string;
};
export type AccountOverview = {
  mainAccount: { id: string; name: string };
  settings: { mode: ManagementMode; autoFund: boolean; revision: number };
  accounts: ManagedAccount[];
  capacity: { available: number; target: number | null };
  transfers: Transfer[];
};
export type ServerIp = {
  address: string | null;
  checkedAt: string | null;
  stale: boolean;
};
export type AccountSnapshot = {
  balance: string;
  available: string;
  realizedPnl: string | null;
  positions: {
    productId: number;
    symbol: string;
    size: string;
    pnl: string | null;
  }[];
  orders: {
    id: number;
    productId: number;
    symbol: string;
    side: string;
    size: string;
  }[];
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function object(value: unknown): Record<string, unknown> {
  if (!isObject(value)) throw new Error("Invalid account response.");
  return value;
}
function string(value: unknown): string {
  if (typeof value !== "string") throw new Error("Invalid account response.");
  return value;
}
function optionalString(value: unknown): string | null {
  return value === null || value === undefined ? null : string(value);
}
function numericText(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  const text = string(value);
  if (!/^-?\d+(\.\d+)?$/.test(text)) throw new Error("Invalid account number.");
  return text;
}
function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Error("Invalid account response.");
  return value;
}
function number(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)
    throw new Error("Invalid account response.");
  return value;
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error("Invalid account response.");
  return value;
}
export function mode(value: unknown): ManagementMode {
  if (value === "main" || value === "automatic" || value === "manual")
    return value;
  throw new Error("Invalid account management mode.");
}
export function participation(value: unknown): Participation {
  if (value === "disabled" || value === "manual" || value === "automatic")
    return value;
  throw new Error("Invalid subaccount participation.");
}
function transferState(value: unknown): Transfer["state"] {
  if (
    value === "prepared" ||
    value === "submitting" ||
    value === "confirmed" ||
    value === "unknown" ||
    value === "failed"
  )
    return value;
  throw new Error("Invalid transfer state.");
}
export function parseOverview(value: unknown): AccountOverview {
  const root = object(value),
    main = object(root.mainAccount),
    settings = object(root.settings),
    capacity = object(root.capacity);
  return {
    mainAccount: { id: string(main.id), name: string(main.name) },
    settings: {
      mode: mode(settings.mode),
      autoFund: boolean(settings.autoFund),
      revision: number(settings.revision),
    },
    capacity: {
      available: number(capacity.available),
      target: capacity.target === null ? null : number(capacity.target),
    },
    accounts: list(root.accounts).map((value) => {
      const row = object(value);
      return {
        id: string(row.id),
        name: string(row.name),
        parentId: string(row.parentId),
        marginMode: optionalString(row.marginMode),
        connected: boolean(row.connected),
        present: boolean(row.present),
        busy: boolean(row.busy),
        participation: participation(row.participation),
        strategyId: optionalString(row.strategyId),
        strategyName: optionalString(row.strategyName),
        budget: optionalString(row.budget),
      };
    }),
    transfers: list(root.transfers).map((value) => {
      const row = object(value);
      return {
        id: string(row.id),
        source_id: string(row.source_id),
        target_id: string(row.target_id),
        amount: string(row.amount),
        state: transferState(row.state),
        error: optionalString(row.error),
        created_at: string(row.created_at),
      };
    }),
  };
}
export function parseServerIp(value: unknown): ServerIp {
  const row = object(value);
  return {
    address: optionalString(row.address),
    checkedAt: optionalString(row.checkedAt),
    stale: boolean(row.stale),
  };
}
export function parseSnapshot(value: unknown): AccountSnapshot {
  const root = object(value);
  return {
    balance: string(root.balance),
    available: string(root.available),
    realizedPnl: optionalString(root.realizedPnl),
    positions: list(root.positions).map((value) => {
      const row = object(value);
      return {
        productId: number(row.product_id),
        symbol: optionalString(row.product_symbol) ?? String(row.product_id),
        size: numericText(row.size),
        pnl:
          row.unrealized_pnl == null ? null : numericText(row.unrealized_pnl),
      };
    }),
    orders: list(root.orders).map((value) => {
      const row = object(value);
      return {
        id: number(row.id),
        productId: number(row.product_id),
        symbol: optionalString(row.product_symbol) ?? String(row.product_id),
        side: string(row.side),
        size: numericText(row.size),
      };
    }),
  };
}

export function parsePendingRuns(
  value: unknown,
): { id: string; name: string }[] {
  return list(object(value).result)
    .map(object)
    .filter(
      (row) =>
        (row.status === "draft" || row.status === "scheduled") &&
        !row.exchangeAccountId,
    )
    .map((row) => ({ id: string(row.id), name: string(row.name) }));
}

export function validTransferAmount(value: string): boolean {
  return /^\d{1,18}(\.\d{1,10})?$/.test(value) && /[1-9]/.test(value);
}
