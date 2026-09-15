-- Run manually in the Supabase SQL editor. Codex does not apply this file.
-- Saved strategy editing and authenticated reads remain available.
-- Live runs are created/changed/deleted through the backend service role.
begin;

revoke insert, update, delete, truncate, references, trigger
  on public.strategies from anon, authenticated;

revoke truncate, references, trigger
  on public.profiles, public.exchange_connections, public.executions,
     public.execution_orders, public.saved_strategies,
     public.capital_settings, public.strategy_capital_slots,
     public.strategy_proposals, public.automation_settings,
     public.automation_agent_runs, public.automation_market_snapshots
  from anon, authenticated;

commit;

-- All three privilege columns should return false for anon and authenticated.
select role_name,
  has_table_privilege(role_name, 'public.strategies', 'INSERT') as can_insert_live_runs,
  has_table_privilege(role_name, 'public.strategies', 'UPDATE') as can_update_live_runs,
  has_table_privilege(role_name, 'public.strategies', 'DELETE') as can_delete_live_runs
from (values ('anon'), ('authenticated')) roles(role_name);
