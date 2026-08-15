-- Full execution metadata for the run history information panel.
-- Run this entire file in Supabase Dashboard > SQL Editor as the project owner.
--
-- Everything the Information panel shows is derived from these columns, so a run
-- stays fully auditable long after the Delta order response has aged out of the
-- exchange APIs. Values are stored as numeric (never rounded floats) because
-- slippage and premium arithmetic must reconcile to the cent.
begin;

alter table public.execution_orders
  add column if not exists order_type text,
  add column if not exists limit_price numeric,
  add column if not exists reference_price numeric,
  add column if not exists contract_value numeric,
  add column if not exists slippage numeric,
  add column if not exists slippage_percent numeric;

comment on column public.execution_orders.order_type is
  'Order type submitted to Delta for this leg (market_order or limit_order).';
comment on column public.execution_orders.limit_price is
  'Limit price submitted for limit orders. Null for market orders.';
comment on column public.execution_orders.reference_price is
  'Mark price observed immediately before submission. The slippage baseline.';
comment on column public.execution_orders.contract_value is
  'Delta contract value for one lot, captured so premium maths survives product changes.';
comment on column public.execution_orders.slippage is
  'Signed execution slippage per unit in quote currency. Positive is adverse: paid more on a buy, received less on a sell.';
comment on column public.execution_orders.slippage_percent is
  'Slippage expressed as a percentage of reference_price.';

alter table public.strategies
  add column if not exists realized_pnl numeric,
  add column if not exists result_json jsonb not null default '{}'::jsonb;

comment on column public.strategies.realized_pnl is
  'Net realized profit or loss in quote currency after commissions, written once the exit is verified.';
comment on column public.strategies.result_json is
  'Settlement summary for the run: premium in and out, commissions, slippage totals, and the per-symbol breakdown.';

commit;
