# This repository's Delta integration

This file documents observed behavior in the current codebase, not intended future behavior.

## Architecture

- Next.js strategy builder: `app/page.tsx`
- Shared TypeScript strategy types: `lib/strategy-types.ts`
- FastAPI models: `backend/app/models.py`
- Delta REST/signing client: `backend/app/delta.py`
- Strike resolution: `backend/app/strategy.py`
- Entry/exit scheduler and execution: `backend/app/engine.py`
- Reusable strategy library migration: `supabase/migrations/003_saved_strategy_library.sql`

## Strategy persistence model

Reusable builder definitions live in `public.saved_strategies` and are protected by per-user RLS policies. Names are labels and are deliberately reusable. The browser reads and writes this library with the signed-in Supabase session, so it remains available when the Python trading backend is offline.

Execution records remain in `public.strategies`. Scheduling always inserts a new immutable run UUID and stores the originating reusable definition in `saved_strategy_id`. Deleting a reusable definition sets that link to null and never deletes execution, order, or run history. A browser-local copy of the selected definition is recovery state, not the canonical multi-strategy library.

The backend uses `Settings.delta_production_url`, signs exact compact JSON, sends a `User-Agent`, and exposes products, option ticker discovery, orders, balances, positions, and cancellation.

## Current entry behavior

The resolver selects separate `call_options` and `put_options` products. Entry then loops over resolved legs and sends one `POST /v2/orders` per leg. The preview correctly warns that legs are sequential and stop after the first failure.

This means the application's default multi-leg strategy is **not** an exchange-listed combo and is **not atomic**.

## Current stop-loss behavior: per leg

Each `StrategyLeg` has its own `stopLoss`. Short legs are rejected if it is absent. During entry, the engine calculates a bracket price independently for each leg:

```python
direction = 1 if position == "buy" else -1
bracket_stop_loss_price = mark - direction * stopLoss
```

For a short option this becomes:

```text
leg stop trigger = preview mark + leg.stopLoss
```

It sends that price as `bracket_stop_loss_price` on the individual product order. Therefore the value is treated as an **absolute price increment**, not a percentage, and the call and put stop independently.

Example: call mark 120, put mark 80, and `stopLoss = 100` on each short leg produce triggers near 220 and 180. That is not a 100% combined-premium stop. The desired combined entry credit is 200 and a 100% aggregate loss trigger would be based on call mark + put mark reaching approximately 400 (before sizes, contract values, fees, and slippage).

## Overall stop loss is not active

`StrategyDefinition.overallStopLoss` exists in the UI/types/model, but `deferred_control_warnings()` labels it as saved for review and not automatically monitored by this scheduler version. The engine never evaluates combined premium or strategy P&L after entry.

The same deferred status applies to overall target, cross-leg break-even trailing, and automatic re-entry.

## Exit behavior

At scheduled exit, the engine reads current positions for products recorded at entry and places sequential reduce-only market orders. This is sounder than reversing the intended entry size, but still permits a partial exit if one call fails.

## Required design for the requested default strategy

To implement “sell ATM call + sell ATM put, stop at 100% of combined filled premium”:

1. Make the default builder create two ATM sell legs.
2. Define risk-unit semantics explicitly (`percent_of_entry_credit` vs absolute currency).
3. After both fills, calculate aggregate entry credit from actual fill prices and contract values.
4. Run a durable monitor using marks/tickers plus REST reconciliation.
5. On aggregate loss threshold, close both live positions reduce-only and cancel their brackets/open orders.
6. Persist trigger and per-leg outcomes; retry/reconcile partial closes.
7. Retain emergency exchange-side per-leg stops because the combined monitor is client-side.
8. Add tests for unequal fills, partial entry, fees, missing prices, reconnect, duplicate trigger, and partial close.

Alternatively, if the exact intended payoff is available as one live `move_options` product and its economics are acceptable, trade that single product. Do not substitute it without verifying payoff, settlement, margin, liquidity, and whether selling it represents the intended short straddle.

## Other observed limitations

- Entry bracket prices use preview `markPrice`, not actual fill price.
- Floating-point arithmetic is used for price calculations instead of `Decimal`/tick quantization.
- A failure after one entry leg does not automatically flatten earlier legs.
- No persistent post-entry strategy risk loop exists.
- No deadman-switch integration is present.
- The initial UI strategy is currently a long ATM call plus short OTM call, named “BTC intraday spread,” not a short ATM straddle.
