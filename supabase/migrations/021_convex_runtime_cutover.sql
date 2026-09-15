-- MANUAL FINAL CUTOVER ONLY, after stopping old application writers,
-- exporting/importing/verifying records, and deploying all new readers.
-- Native Agno tables in schema ai and Supabase Auth remain unchanged.
-- Old application tables are retained for verified rollback/export, not deleted.
begin;

revoke insert, update, delete, truncate, references, trigger
  on public.profiles, public.exchange_connections, public.strategies,
     public.executions, public.execution_orders, public.strategy_capital_slots,
     public.strategy_proposals, public.automation_settings,
     public.automation_agent_runs, public.automation_market_snapshots,
     public.capital_settings
  from anon, authenticated;

-- The old service role remains able to read the retained tables for recovery.
-- Do not revoke the broad service role during a mixed-version rollout.
commit;
