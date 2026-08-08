-- Combined-premium strategy monitoring state and fill reconciliation.
begin;

alter table public.strategies
  add column if not exists risk_state jsonb not null default '{}'::jsonb,
  add column if not exists risk_monitor_at timestamptz,
  add column if not exists combined_stop_triggered_at timestamptz;

alter table public.execution_orders
  add column if not exists filled_size numeric not null default 0,
  add column if not exists average_fill_price numeric,
  add column if not exists commission numeric not null default 0;

create index if not exists strategies_active_risk_idx
  on public.strategies(status, risk_monitor_at)
  where status = 'active' and combined_stop_triggered_at is null;

commit;
