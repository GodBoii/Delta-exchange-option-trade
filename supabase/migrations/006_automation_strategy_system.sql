-- Versioned strategy definitions and the read-only-first automation workflow.
begin;

alter table public.saved_strategies
  add column if not exists version integer not null default 1 check (version >= 1),
  add column if not exists enabled_for_ai boolean not null default false;

create or replace function public.bump_saved_strategy_version()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.definition_json is distinct from new.definition_json
     or old.name is distinct from new.name then
    new.version = old.version + 1;
  end if;
  new.enabled_for_ai = coalesce((new.definition_json ->> 'enabledForAi')::boolean, new.enabled_for_ai);
  return new;
end;
$$;

drop trigger if exists saved_strategies_version on public.saved_strategies;
create trigger saved_strategies_version
before update on public.saved_strategies
for each row execute function public.bump_saved_strategy_version();

update public.saved_strategies
set enabled_for_ai = coalesce((definition_json ->> 'enabledForAi')::boolean, false)
where enabled_for_ai is distinct from coalesce((definition_json ->> 'enabledForAi')::boolean, false);

create table if not exists public.automation_settings (
  user_id uuid primary key references auth.users(id) on delete cascade,
  enabled boolean not null default false,
  model_id text not null default 'deepseek/deepseek-v4-flash-vision-exp',
  minimum_follow_up_minutes integer not null default 5 check (minimum_follow_up_minutes between 5 and 1440),
  maximum_agent_runs_per_day integer not null default 12 check (maximum_agent_runs_per_day between 1 and 48),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.automation_market_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source text not null default 'automation_agent',
  market_json jsonb not null,
  account_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.automation_agent_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  run_key text,
  trigger text not null check (trigger in ('manual', 'asia_session', 'london_session', 'new_york_session', 'agent_follow_up', 'activation_recheck')),
  status text not null default 'scheduled' check (status in ('scheduled', 'running', 'completed', 'failed', 'cancelled')),
  outcome text check (outcome in ('strategy_selected', 'wait_and_run_again', 'no_trade_for_current_window')),
  scheduled_for timestamptz not null,
  started_at timestamptz,
  completed_at timestamptz,
  model_id text not null default 'deepseek/deepseek-v4-flash-vision-exp',
  agno_session_id text,
  agno_run_id text,
  reason text,
  signals_to_inspect jsonb not null default '[]'::jsonb,
  market_snapshot_id uuid references public.automation_market_snapshots(id) on delete set null,
  news_analysis_id text,
  report_markdown text,
  member_responses jsonb not null default '[]'::jsonb,
  tool_calls jsonb not null default '[]'::jsonb,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, run_key)
);

create table if not exists public.strategy_proposals (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  agent_run_id uuid references public.automation_agent_runs(id) on delete set null,
  strategy_id uuid references public.strategies(id) on delete set null,
  saved_strategy_id uuid not null references public.saved_strategies(id) on delete restrict,
  saved_strategy_version integer not null check (saved_strategy_version >= 1),
  status text not null default 'proposed' check (status in ('proposed', 'scheduled', 'rejected', 'expired', 'activated', 'cancelled')),
  activation_time timestamptz not null,
  proposal_expiry timestamptz not null,
  ai_confidence numeric(5,4) not null check (ai_confidence between 0 and 1),
  reasoning_summary text not null,
  supporting_signals jsonb not null default '[]'::jsonb,
  invalidation_signals jsonb not null default '[]'::jsonb,
  market_snapshot_id uuid references public.automation_market_snapshots(id) on delete set null,
  news_analysis_id text,
  rejection_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (proposal_expiry > activation_time)
);

create table if not exists public.strategy_capital_slots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  slot_number smallint not null check (slot_number between 1 and 3),
  strategy_id uuid references public.strategies(id) on delete set null,
  proposal_id uuid references public.strategy_proposals(id) on delete set null,
  status text not null default 'available' check (status in ('available', 'reserved', 'active')),
  reserved_at timestamptz,
  released_at timestamptz,
  updated_at timestamptz not null default now(),
  unique (user_id, slot_number),
  check (
    (status = 'available' and strategy_id is null and proposal_id is null)
    or (status in ('reserved', 'active') and (strategy_id is not null or proposal_id is not null))
  )
);

create index if not exists automation_runs_due_idx
  on public.automation_agent_runs(scheduled_for)
  where status = 'scheduled';
create index if not exists automation_runs_user_created_idx
  on public.automation_agent_runs(user_id, created_at desc);
create index if not exists strategy_proposals_user_created_idx
  on public.strategy_proposals(user_id, created_at desc);
create index if not exists strategy_proposals_activation_idx
  on public.strategy_proposals(activation_time)
  where status in ('proposed', 'scheduled');
create index if not exists automation_snapshots_user_created_idx
  on public.automation_market_snapshots(user_id, created_at desc);

drop trigger if exists automation_settings_updated_at on public.automation_settings;
create trigger automation_settings_updated_at before update on public.automation_settings
for each row execute function public.set_updated_at();
drop trigger if exists automation_runs_updated_at on public.automation_agent_runs;
create trigger automation_runs_updated_at before update on public.automation_agent_runs
for each row execute function public.set_updated_at();
drop trigger if exists strategy_proposals_updated_at on public.strategy_proposals;
create trigger strategy_proposals_updated_at before update on public.strategy_proposals
for each row execute function public.set_updated_at();
drop trigger if exists strategy_capital_slots_updated_at on public.strategy_capital_slots;
create trigger strategy_capital_slots_updated_at before update on public.strategy_capital_slots
for each row execute function public.set_updated_at();

alter table public.automation_settings enable row level security;
alter table public.automation_market_snapshots enable row level security;
alter table public.automation_agent_runs enable row level security;
alter table public.strategy_proposals enable row level security;
alter table public.strategy_capital_slots enable row level security;

drop policy if exists automation_settings_select_own on public.automation_settings;
create policy automation_settings_select_own on public.automation_settings for select to authenticated
using ((select auth.uid()) = user_id);
drop policy if exists automation_settings_insert_own on public.automation_settings;
create policy automation_settings_insert_own on public.automation_settings for insert to authenticated
with check ((select auth.uid()) = user_id);
drop policy if exists automation_settings_update_own on public.automation_settings;
create policy automation_settings_update_own on public.automation_settings for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists automation_snapshots_select_own on public.automation_market_snapshots;
create policy automation_snapshots_select_own on public.automation_market_snapshots for select to authenticated
using ((select auth.uid()) = user_id);
drop policy if exists automation_runs_select_own on public.automation_agent_runs;
create policy automation_runs_select_own on public.automation_agent_runs for select to authenticated
using ((select auth.uid()) = user_id);
drop policy if exists strategy_proposals_select_own on public.strategy_proposals;
create policy strategy_proposals_select_own on public.strategy_proposals for select to authenticated
using ((select auth.uid()) = user_id);
drop policy if exists strategy_slots_select_own on public.strategy_capital_slots;
create policy strategy_slots_select_own on public.strategy_capital_slots for select to authenticated
using ((select auth.uid()) = user_id);

grant select on public.automation_market_snapshots, public.automation_agent_runs,
  public.strategy_proposals, public.strategy_capital_slots to authenticated;
grant select, insert, update on public.automation_settings to authenticated;
grant all on public.automation_settings, public.automation_market_snapshots,
  public.automation_agent_runs, public.strategy_proposals, public.strategy_capital_slots to service_role;

commit;
