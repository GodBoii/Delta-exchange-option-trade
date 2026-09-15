-- Disposable PostgreSQL test only. Never run this fixture against Supabase.
create role anon;
create role authenticated;
create role service_role;
create table public.saved_strategies (id uuid primary key);
create table public.strategies (
  id uuid primary key, saved_strategy_id uuid references public.saved_strategies(id)
);
create table public.strategy_proposals (
  id uuid primary key, saved_strategy_id uuid references public.saved_strategies(id)
);
create table public.profiles (id uuid primary key);
create table public.exchange_connections (id uuid primary key);
create table public.executions (id uuid primary key);
create table public.execution_orders (id uuid primary key);
create table public.capital_settings (id uuid primary key);
create table public.strategy_capital_slots (id uuid primary key);
create table public.automation_settings (id uuid primary key);
create table public.automation_agent_runs (id uuid primary key);
create table public.automation_market_snapshots (id uuid primary key);
grant all on all tables in schema public to anon, authenticated, service_role;
insert into public.saved_strategies values ('11111111-1111-4111-8111-111111111111');
insert into public.strategies values ('22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111');

\i /audit/supabase/migrations/019_restrict_live_strategy_client_writes.sql
do $$ begin
  if has_table_privilege('authenticated', 'public.strategies', 'UPDATE')
     or has_table_privilege('anon', 'public.strategies', 'DELETE') then
    raise exception 'Live client writes remain allowed';
  end if;
  if not has_table_privilege('authenticated', 'public.saved_strategies', 'UPDATE')
     or not has_table_privilege('authenticated', 'public.strategies', 'SELECT')
     or not has_table_privilege('service_role', 'public.strategies', 'UPDATE') then
    raise exception 'Existing library, history or backend access was removed';
  end if;
end $$;

\i /audit/supabase/migrations/020_convex_library_reference_boundary.sql
do $$ begin
  if has_table_privilege('authenticated', 'public.saved_strategies', 'UPDATE') then
    raise exception 'Retired library remains writable by browser';
  end if;
  if (select count(*) from public.strategies) <> 1
     or (select count(*) from public.saved_strategies) <> 1 then
    raise exception 'Cutover changed historical records';
  end if;
  if exists (select 1 from pg_constraint where conname in (
      'strategies_saved_strategy_id_fkey', 'strategy_proposals_saved_strategy_id_fkey')) then
    raise exception 'Old library references remain';
  end if;
end $$;
select 'Permission and reference tests passed' as result;
