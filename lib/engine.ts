import crypto from "node:crypto";
import { db, type AccountRow } from "@/lib/db";
import { DeltaClient } from "@/lib/delta";
import { credentialsFor } from "@/lib/auth";
import { deltaExpiry, resolveLeg, strategySchema, type ResolvedLeg, type StrategyDefinition } from "@/lib/strategy";
import { AppError } from "@/lib/http";

type StrategyRow = { id: string; account_id: string; name: string; status: string; definition_json: string; entry_at: string; exit_at: string; entry_execution_at: string | null; exit_execution_at: string | null };

async function resolveStrategy(client: DeltaClient, definition: StrategyDefinition) {
  const byExpiry = new Map<string, Awaited<ReturnType<DeltaClient["optionChain"]>>["result"]>();
  const resolved: ResolvedLeg[] = [];
  for (const leg of definition.legs) {
    let chain = byExpiry.get(leg.expiry);
    if (!chain) {
      chain = (await client.optionChain(definition.instrument.underlying, deltaExpiry(leg.expiry))).result;
      byExpiry.set(leg.expiry, chain);
    }
    resolved.push(resolveLeg(leg, chain));
  }
  return resolved;
}

export async function previewStrategy(client: DeltaClient, raw: unknown) {
  const definition = strategySchema.parse(raw);
  const legs = await resolveStrategy(client, definition);
  const deferredControls = [
    ...(definition.overallTarget ? ["overall target"] : []),
    ...(definition.overallStopLoss ? ["overall stop loss"] : []),
    ...(definition.trailToBreakEven ? ["cross-leg break-even trailing"] : []),
    ...(definition.legs.some((leg) => leg.reentryOnTarget || leg.reentryOnStop) ? ["automatic re-entry"] : [])
  ];
  return {
    definition,
    legs,
    warnings: [
      "Delta Exchange cannot atomically batch different option contracts. Legs execute sequentially and stop after the first failure.",
      "Market orders may fill at prices different from the preview, especially in thin option books.",
      ...(deferredControls.length ? [`${deferredControls.join(", ")} settings are saved for review but are not automatically monitored by this worker version.`] : [])
    ]
  };
}

export function saveStrategy(accountId: string, raw: unknown, status: "draft" | "scheduled") {
  const definition = strategySchema.parse(raw);
  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  db.prepare(`INSERT INTO strategies (id, account_id, name, status, definition_json, entry_at, exit_at, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(id, accountId, definition.name, status, JSON.stringify(definition), definition.entry.entryAt, definition.entry.exitAt, now, now);
  return { id, status };
}

function accountForStrategy(row: StrategyRow) {
  const account = db.prepare("SELECT * FROM accounts WHERE id = ?").get(row.account_id) as AccountRow | undefined;
  if (!account) throw new AppError(404, "Connected account no longer exists", "account_not_found");
  return account;
}

export async function executeEntry(strategyId: string) {
  const row = db.prepare("SELECT * FROM strategies WHERE id = ?").get(strategyId) as StrategyRow | undefined;
  if (!row) throw new AppError(404, "Strategy not found", "strategy_not_found");
  if (row.entry_execution_at) throw new AppError(409, "Strategy entry has already run", "already_executed");
  const account = accountForStrategy(row);
  const client = new DeltaClient(credentialsFor(account));
  const definition = strategySchema.parse(JSON.parse(row.definition_json));
  const resolved = await resolveStrategy(client, definition);
  const claimed = db.prepare("UPDATE strategies SET status='executing_entry', updated_at=? WHERE id=? AND entry_execution_at IS NULL AND status IN ('draft','scheduled')")
    .run(new Date().toISOString(), strategyId);
  if (!claimed.changes) throw new AppError(409, "Strategy entry is already running or cannot be executed", "execution_in_progress");
  const executionId = crypto.randomUUID();
  const started = new Date().toISOString();
  db.prepare("INSERT INTO executions (id, strategy_id, kind, status, started_at) VALUES (?, ?, 'entry', 'running', ?)").run(executionId, strategyId, started);
  let failure: Error | null = null;
  for (const [index, leg] of resolved.entries()) {
    const clientOrderId = `ds_${strategyId.slice(0, 8)}_${index}_${Date.now().toString(36)}`.slice(0, 32);
    try {
      const mark = Number(leg.markPrice);
      const direction = leg.position === "buy" ? 1 : -1;
      const bracket = Number.isFinite(mark) && mark > 0 ? {
        ...(leg.targetProfit ? { bracket_take_profit_price: String(Math.max(0.00000001, mark + direction * leg.targetProfit)) } : {}),
        ...(leg.stopLoss ? { bracket_stop_loss_price: String(Math.max(0.00000001, mark - direction * leg.stopLoss)) } : {}),
        ...(leg.trailStop ? { bracket_trail_amount: String(leg.trailStop) } : {}),
        ...((leg.targetProfit || leg.stopLoss) ? { bracket_stop_trigger_method: "mark_price" } : {})
      } : {};
      const order = await client.placeOrder({
        product_id: leg.productId,
        product_symbol: leg.productSymbol,
        size: leg.lots,
        side: leg.position,
        order_type: leg.orderType,
        ...(leg.orderType === "limit_order" ? { limit_price: leg.limitPrice } : {}),
        time_in_force: "gtc",
        reduce_only: false,
        client_order_id: clientOrderId,
        ...bracket
      });
      const result = order.result as Record<string, unknown>;
      db.prepare(`INSERT INTO execution_orders (id, execution_id, leg_id, delta_order_id, client_order_id, product_id, product_symbol, side, size, state, response_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
        .run(crypto.randomUUID(), executionId, leg.id, String(result.id ?? ""), clientOrderId, leg.productId, leg.productSymbol, leg.position, leg.lots, String(result.state ?? "submitted"), JSON.stringify(order), new Date().toISOString());
    } catch (error) {
      failure = error instanceof Error ? error : new Error("Unknown order failure");
      db.prepare(`INSERT INTO execution_orders (id, execution_id, leg_id, client_order_id, product_id, product_symbol, side, size, state, response_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?)`)
        .run(crypto.randomUUID(), executionId, leg.id, clientOrderId, leg.productId, leg.productSymbol, leg.position, leg.lots, JSON.stringify({ error: failure.message }), new Date().toISOString());
      break;
    }
  }
  const completed = new Date().toISOString();
  db.prepare("UPDATE executions SET status=?, error=?, completed_at=? WHERE id=?")
    .run(failure ? "partial_or_failed" : "completed", failure?.message ?? null, completed, executionId);
  db.prepare("UPDATE strategies SET status=?, entry_execution_at=?, last_error=?, updated_at=? WHERE id=?")
    .run(failure ? "attention" : "active", completed, failure?.message ?? null, completed, strategyId);
  if (failure) throw failure;
  return { executionId, legs: resolved.length };
}

export async function executeExit(strategyId: string) {
  const row = db.prepare("SELECT * FROM strategies WHERE id = ?").get(strategyId) as StrategyRow | undefined;
  if (!row) throw new AppError(404, "Strategy not found", "strategy_not_found");
  if (row.exit_execution_at) throw new AppError(409, "Strategy exit has already run", "already_exited");
  const account = accountForStrategy(row);
  const client = new DeltaClient(credentialsFor(account));
  const executedProducts = db.prepare(`SELECT DISTINCT eo.product_id, eo.product_symbol
    FROM execution_orders eo JOIN executions e ON e.id=eo.execution_id
    WHERE e.strategy_id=? AND e.kind='entry' AND eo.state != 'failed'`).all(strategyId) as { product_id: number; product_symbol: string }[];
  const positions = (await client.positions()).result as { product_id: number; size: number | string; product_symbol?: string }[];
  const claimed = db.prepare("UPDATE strategies SET status='executing_exit', updated_at=? WHERE id=? AND exit_execution_at IS NULL AND status='active'")
    .run(new Date().toISOString(), strategyId);
  if (!claimed.changes) throw new AppError(409, "Strategy exit is already running or cannot be executed", "exit_in_progress");
  const executionId = crypto.randomUUID();
  const started = new Date().toISOString();
  db.prepare("INSERT INTO executions (id, strategy_id, kind, status, started_at) VALUES (?, ?, 'exit', 'running', ?)").run(executionId, strategyId, started);
  let failure: Error | null = null;
  for (const product of executedProducts) {
    const position = positions.find((item) => Number(item.product_id) === product.product_id);
    const size = Number(position?.size ?? 0);
    if (!Number.isFinite(size) || size === 0) continue;
    const clientOrderId = `dx_${strategyId.slice(0, 8)}_${Date.now().toString(36)}`.slice(0, 32);
    try {
      const order = await client.placeOrder({ product_id: product.product_id, product_symbol: product.product_symbol, size: Math.abs(size), side: size > 0 ? "sell" : "buy", order_type: "market_order", reduce_only: true, client_order_id: clientOrderId });
      const result = order.result as Record<string, unknown>;
      db.prepare(`INSERT INTO execution_orders (id, execution_id, leg_id, delta_order_id, client_order_id, product_id, product_symbol, side, size, state, response_json, created_at)
        VALUES (?, ?, 'exit', ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
        .run(crypto.randomUUID(), executionId, String(result.id ?? ""), clientOrderId, product.product_id, product.product_symbol, size > 0 ? "sell" : "buy", Math.abs(size), String(result.state ?? "submitted"), JSON.stringify(order), new Date().toISOString());
    } catch (error) { failure = error instanceof Error ? error : new Error("Exit failed"); break; }
  }
  const completed = new Date().toISOString();
  db.prepare("UPDATE executions SET status=?, error=?, completed_at=? WHERE id=?").run(failure ? "partial_or_failed" : "completed", failure?.message ?? null, completed, executionId);
  db.prepare("UPDATE strategies SET status=?, exit_execution_at=?, last_error=?, updated_at=? WHERE id=?").run(failure ? "attention" : "completed", completed, failure?.message ?? null, completed, strategyId);
  if (failure) throw failure;
  return { executionId };
}

export async function processDueStrategies() {
  const now = new Date().toISOString();
  const dueEntries = db.prepare("SELECT id FROM strategies WHERE status='scheduled' AND entry_execution_at IS NULL AND entry_at <= ?").all(now) as { id: string }[];
  for (const row of dueEntries) await executeEntry(row.id).catch((error) => console.error("entry", row.id, error));
  const dueExits = db.prepare("SELECT id FROM strategies WHERE status='active' AND exit_execution_at IS NULL AND exit_at <= ?").all(now) as { id: string }[];
  for (const row of dueExits) await executeExit(row.id).catch((error) => console.error("exit", row.id, error));
}
