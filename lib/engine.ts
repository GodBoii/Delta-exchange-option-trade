import { DeltaClient } from "@/lib/delta";
import { credentialsForUser } from "@/lib/auth";
import { getSupabaseAdmin } from "@/lib/supabase/admin";
import type { Database } from "@/lib/supabase/types";
import { deltaExpiry, resolveLeg, strategySchema, type ResolvedLeg, type StrategyDefinition } from "@/lib/strategy";
import { AppError } from "@/lib/http";

type StrategyRow = {
  id: string;
  user_id: string;
  name: string;
  status: string;
  definition_json: unknown;
  entry_at: string | null;
  exit_at: string | null;
  entry_execution_at: string | null;
  exit_execution_at: string | null;
};

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

export async function saveStrategy(userId: string, raw: unknown, status: "draft" | "scheduled") {
  const definition = strategySchema.parse(raw);
  const admin = getSupabaseAdmin();
  const { data, error } = await admin.from("strategies").insert({
    user_id: userId,
    name: definition.name,
    status,
    definition_json: definition,
    entry_at: definition.entry.entryAt,
    exit_at: definition.entry.exitAt
  }).select("id,status").single();
  if (error || !data) throw new AppError(500, "Could not save the strategy", "strategy_save_failed");
  return data;
}

async function strategyById(strategyId: string) {
  const { data, error } = await getSupabaseAdmin().from("strategies").select("*").eq("id", strategyId).maybeSingle();
  if (error) throw new AppError(500, "Could not load the strategy", "strategy_lookup_failed");
  if (!data) throw new AppError(404, "Strategy not found", "strategy_not_found");
  return data as unknown as StrategyRow;
}

async function claimStrategy(strategyId: string, statuses: string[], nextStatus: string, executionField: "entry_execution_at" | "exit_execution_at") {
  const admin = getSupabaseAdmin();
  const { data, error } = await admin.from("strategies")
    .update({ status: nextStatus })
    .eq("id", strategyId)
    .is(executionField, null)
    .in("status", statuses)
    .select("id")
    .maybeSingle();
  if (error) throw new AppError(500, "Could not claim the strategy execution", "execution_claim_failed");
  if (!data) throw new AppError(409, "Strategy is already running or cannot be executed", "execution_in_progress");
}

type ExecutionOrderInsert = Database["public"]["Tables"]["execution_orders"]["Insert"];

async function recordOrder(order: ExecutionOrderInsert) {
  const { error } = await getSupabaseAdmin().from("execution_orders").insert(order);
  if (error) throw new AppError(500, "Could not record an order result", "order_record_failed");
}

export async function executeEntry(strategyId: string) {
  const row = await strategyById(strategyId);
  if (row.entry_execution_at) throw new AppError(409, "Strategy entry has already run", "already_executed");
  const client = new DeltaClient(await credentialsForUser(row.user_id));
  const definition = strategySchema.parse(row.definition_json);
  const resolved = await resolveStrategy(client, definition);
  await claimStrategy(strategyId, ["draft", "scheduled"], "executing_entry", "entry_execution_at");
  const admin = getSupabaseAdmin();
  const { data: execution, error: executionError } = await admin.from("executions").insert({ strategy_id: strategyId, kind: "entry", status: "running" }).select("id,started_at").single();
  if (executionError || !execution) throw new AppError(500, "Could not start the execution record", "execution_record_failed");
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
      await recordOrder({ execution_id: execution.id, leg_id: leg.id, delta_order_id: String(result.id ?? ""), client_order_id: clientOrderId, product_id: leg.productId, product_symbol: leg.productSymbol, side: leg.position, size: leg.lots, state: String(result.state ?? "submitted"), response_json: JSON.parse(JSON.stringify(order)) });
    } catch (error) {
      failure = error instanceof Error ? error : new Error("Unknown order failure");
      await recordOrder({ execution_id: execution.id, leg_id: leg.id, client_order_id: clientOrderId, product_id: leg.productId, product_symbol: leg.productSymbol, side: leg.position, size: leg.lots, state: "failed", response_json: { error: failure.message } });
      break;
    }
  }
  const completed = new Date().toISOString();
  await Promise.all([
    admin.from("executions").update({ status: failure ? "partial_or_failed" : "completed", error: failure?.message ?? null, completed_at: completed }).eq("id", execution.id),
    admin.from("strategies").update({ status: failure ? "attention" : "active", entry_execution_at: completed, last_error: failure?.message ?? null }).eq("id", strategyId)
  ]);
  if (failure) throw failure;
  return { executionId: execution.id, legs: resolved.length };
}

export async function executeExit(strategyId: string) {
  const row = await strategyById(strategyId);
  if (row.exit_execution_at) throw new AppError(409, "Strategy exit has already run", "already_exited");
  const client = new DeltaClient(await credentialsForUser(row.user_id));
  const admin = getSupabaseAdmin();
  const { data: entryExecutions } = await admin.from("executions").select("id").eq("strategy_id", strategyId).eq("kind", "entry");
  const executionIds = (entryExecutions ?? []).map((item) => item.id);
  const { data: recordedOrders } = executionIds.length
    ? await admin.from("execution_orders").select("product_id,product_symbol,state").in("execution_id", executionIds).neq("state", "failed")
    : { data: [] as { product_id: number; product_symbol: string; state: string }[] };
  const uniqueProducts = Array.from(new Map((recordedOrders ?? []).map((item) => [Number(item.product_id), { product_id: Number(item.product_id), product_symbol: item.product_symbol }])).values());
  const positions = (await client.positions()).result as { product_id: number; size: number | string; product_symbol?: string }[];
  await claimStrategy(strategyId, ["active"], "executing_exit", "exit_execution_at");
  const { data: execution, error: executionError } = await admin.from("executions").insert({ strategy_id: strategyId, kind: "exit", status: "running" }).select("id").single();
  if (executionError || !execution) throw new AppError(500, "Could not start the exit record", "execution_record_failed");
  let failure: Error | null = null;
  for (const product of uniqueProducts) {
    const position = positions.find((item) => Number(item.product_id) === product.product_id);
    const size = Number(position?.size ?? 0);
    if (!Number.isFinite(size) || size === 0) continue;
    const clientOrderId = `dx_${strategyId.slice(0, 8)}_${Date.now().toString(36)}`.slice(0, 32);
    try {
      const side = size > 0 ? "sell" : "buy";
      const order = await client.placeOrder({ product_id: product.product_id, product_symbol: product.product_symbol, size: Math.abs(size), side, order_type: "market_order", reduce_only: true, client_order_id: clientOrderId });
      const result = order.result as Record<string, unknown>;
      await recordOrder({ execution_id: execution.id, leg_id: "exit", delta_order_id: String(result.id ?? ""), client_order_id: clientOrderId, product_id: product.product_id, product_symbol: product.product_symbol, side, size: Math.abs(size), state: String(result.state ?? "submitted"), response_json: JSON.parse(JSON.stringify(order)) });
    } catch (error) { failure = error instanceof Error ? error : new Error("Exit failed"); break; }
  }
  const completed = new Date().toISOString();
  await Promise.all([
    admin.from("executions").update({ status: failure ? "partial_or_failed" : "completed", error: failure?.message ?? null, completed_at: completed }).eq("id", execution.id),
    admin.from("strategies").update({ status: failure ? "attention" : "completed", exit_execution_at: completed, last_error: failure?.message ?? null }).eq("id", strategyId)
  ]);
  if (failure) throw failure;
  return { executionId: execution.id };
}

export async function processDueStrategies() {
  const admin = getSupabaseAdmin();
  const now = new Date().toISOString();
  const [{ data: dueEntries }, { data: dueExits }] = await Promise.all([
    admin.from("strategies").select("id").eq("status", "scheduled").is("entry_execution_at", null).lte("entry_at", now).limit(25),
    admin.from("strategies").select("id").eq("status", "active").is("exit_execution_at", null).lte("exit_at", now).limit(25)
  ]);
  for (const row of dueEntries ?? []) await executeEntry(row.id).catch((error) => console.error("entry", row.id, error));
  for (const row of dueExits ?? []) await executeExit(row.id).catch((error) => console.error("exit", row.id, error));
}
