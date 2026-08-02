import crypto from "node:crypto";
import { getDeltaBaseUrl, type DeltaEnvironment } from "@/lib/config";
import { AppError } from "@/lib/http";

type DeltaEnvelope<T> = { success: boolean; result: T; meta?: Record<string, unknown>; error?: { code?: string; message?: string; context?: unknown } };

export type DeltaCredentials = { apiKey: string; apiSecret: string; environment: DeltaEnvironment };

export type DeltaProduct = {
  id: number;
  symbol: string;
  description?: string;
  contract_type: "perpetual_futures" | "futures" | "call_options" | "put_options" | string;
  state: string;
  trading_status: string;
  settlement_time?: string | null;
  contract_value?: string;
  tick_size?: string;
  underlying_asset?: { symbol: string };
  spot_index?: { symbol: string };
};

export type DeltaTicker = {
  product_id: number;
  symbol: string;
  contract_type: string;
  strike_price?: string;
  spot_price?: string;
  mark_price?: string;
  quotes?: { best_bid?: string; best_ask?: string; bid_size?: string; ask_size?: string };
};

export type DeltaProfile = { id: string | number; account_name?: string; email?: string; margin_mode?: string; first_name?: string };

function canonicalQuery(params?: Record<string, string | number | boolean | undefined>) {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined) search.append(key, String(value));
  const value = search.toString();
  return value ? `?${value}` : "";
}

export class DeltaClient {
  constructor(private credentials?: DeltaCredentials) {}

  private async request<T>(method: string, path: string, options: {
    query?: Record<string, string | number | boolean | undefined>;
    body?: unknown;
    authenticated?: boolean;
  } = {}): Promise<DeltaEnvelope<T>> {
    const query = canonicalQuery(options.query);
    const payload = options.body === undefined ? "" : JSON.stringify(options.body);
    const environment = this.credentials?.environment ?? "production";
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json",
      "User-Agent": "delta-strategy-desk/0.1"
    };
    if (options.authenticated) {
      if (!this.credentials) throw new AppError(401, "Delta connection required", "not_connected");
      const timestamp = Math.floor(Date.now() / 1000).toString();
      const prehash = method + timestamp + path + query + payload;
      headers["api-key"] = this.credentials.apiKey;
      headers.timestamp = timestamp;
      headers.signature = crypto.createHmac("sha256", this.credentials.apiSecret).update(prehash).digest("hex");
    }
    let response: Response;
    try {
      response = await fetch(`${getDeltaBaseUrl(environment)}${path}${query}`, {
        method,
        headers,
        body: payload || undefined,
        cache: "no-store",
        signal: AbortSignal.timeout(12_000)
      });
    } catch (error) {
      throw new AppError(502, error instanceof Error ? error.message : "Delta Exchange is unreachable", "delta_unreachable");
    }
    const data = (await response.json().catch(() => ({ success: false, error: { message: `Delta returned HTTP ${response.status}` } }))) as DeltaEnvelope<T>;
    if (!response.ok || data.success === false) {
      const code = data.error?.code ?? `delta_http_${response.status}`;
      const message = data.error?.message ?? String(code).replaceAll("_", " ");
      throw new AppError(response.status >= 400 && response.status < 500 ? response.status : 502, message, String(code));
    }
    return data;
  }

  profile() { return this.request<DeltaProfile>("GET", "/v2/profile", { authenticated: true }); }
  balances() { return this.request<unknown[]>("GET", "/v2/wallet/balances", { authenticated: true }); }
  openOrders() { return this.request<unknown[]>("GET", "/v2/orders", { query: { state: "open,pending", page_size: 50 }, authenticated: true }); }
  positions() { return this.request<unknown[]>("GET", "/v2/positions/margined", { authenticated: true }); }
  products(query: Record<string, string | number | undefined> = {}) { return this.request<DeltaProduct[]>("GET", "/v2/products", { query }); }
  product(symbol: string) { return this.request<DeltaProduct>("GET", `/v2/products/${encodeURIComponent(symbol)}`); }
  optionChain(underlying: string, expiry: string) {
    return this.request<DeltaTicker[]>("GET", "/v2/tickers", {
      query: { contract_types: "call_options,put_options", underlying_asset_symbols: underlying, expiry_date: expiry }
    });
  }
  ticker(symbol: string) { return this.request<DeltaTicker>("GET", `/v2/tickers/${encodeURIComponent(symbol)}`); }
  placeOrder(order: Record<string, unknown>) { return this.request<Record<string, unknown>>("POST", "/v2/orders", { body: order, authenticated: true }); }
  cancelOrder(id: number, productId: number) { return this.request<unknown>("DELETE", "/v2/orders", { body: { id, product_id: productId }, authenticated: true }); }
}
