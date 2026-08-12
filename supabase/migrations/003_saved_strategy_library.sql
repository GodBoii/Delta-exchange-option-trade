-- Persistent, user-scoped strategy library separate from immutable execution runs.
-- Run this entire file in Supabase Dashboard > SQL Editor as the project owner.

begin;

create table if not exists public.saved_strategies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(trim(name)) between 2 and 80),
  definition_json jsonb not null check (jsonb_typeof(definition_json) = 'object'),
  source_run_id uuid unique references public.strategies(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.strategies
  add column if not exists saved_strategy_id uuid references public.saved_strategies(id) on delete set null;

comment on table public.saved_strategies is
  'Reusable builder definitions. Names are labels and are intentionally not unique.';
comment on column public.saved_strategies.source_run_id is
  'Set only when an existing historical run is backfilled into the reusable library.';
comment on column public.strategies.saved_strategy_id is
  'Optional reusable strategy definition from which this immutable run was scheduled.';

create index if not exists saved_strategies_user_updated_idx
  on public.saved_strategies(user_id, updated_at desc);
create index if not exists strategies_saved_strategy_idx
  on public.strategies(saved_strategy_id)
  where saved_strategy_id is not null;

create or replace function public.validate_saved_strategy_source_run()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.source_run_id is not null and not exists (
    select 1 from public.strategies run
    where run.id = new.source_run_id and run.user_id = new.user_id
  ) then
    raise exception 'source run must belong to the saved strategy owner';
  end if;
  return new;
end;
$$;

create or replace function public.validate_strategy_saved_definition()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.saved_strategy_id is not null and not exists (
    select 1 from public.saved_strategies saved
    where saved.id = new.saved_strategy_id and saved.user_id = new.user_id
  ) then
    raise exception 'saved strategy must belong to the run owner';
  end if;
  return new;
end;
$$;

drop trigger if exists saved_strategies_validate_source_run on public.saved_strategies;
create trigger saved_strategies_validate_source_run
before insert or update on public.saved_strategies
for each row execute function public.validate_saved_strategy_source_run();

drop trigger if exists strategies_validate_saved_definition on public.strategies;
create trigger strategies_validate_saved_definition
before insert or update on public.strategies
for each row execute function public.validate_strategy_saved_definition();

drop trigger if exists saved_strategies_updated_at on public.saved_strategies;
create trigger saved_strategies_updated_at
before update on public.saved_strategies
for each row execute function public.set_updated_at();

alter table public.saved_strategies enable row level security;

drop policy if exists "saved_strategies_select_own" on public.saved_strategies;
create policy "saved_strategies_select_own"
on public.saved_strategies for select to authenticated
using ((select auth.uid()) = user_id);

drop policy if exists "saved_strategies_insert_own" on public.saved_strategies;
create policy "saved_strategies_insert_own"
on public.saved_strategies for insert to authenticated
with check ((select auth.uid()) = user_id);

drop policy if exists "saved_strategies_update_own" on public.saved_strategies;
create policy "saved_strategies_update_own"
on public.saved_strategies for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

drop policy if exists "saved_strategies_delete_own" on public.saved_strategies;
create policy "saved_strategies_delete_own"
on public.saved_strategies for delete to authenticated
using ((select auth.uid()) = user_id);

grant select, delete on public.saved_strategies to authenticated;
grant insert (user_id, name, definition_json) on public.saved_strategies to authenticated;
grant update (name, definition_json) on public.saved_strategies to authenticated;
grant all on public.saved_strategies to service_role;

-- Make every pre-library run available as a reusable strategy. source_run_id keeps
-- this backfill idempotent, and new linked runs are excluded on subsequent runs.
insert into public.saved_strategies (
  user_id,
  name,
  definition_json,
  source_run_id,
  created_at,
  updated_at
)
select
  run.user_id,
  run.name,
  run.definition_json,
  run.id,
  run.created_at,
  run.updated_at
from public.strategies run
where run.saved_strategy_id is null
on conflict (source_run_id) do nothing;

update public.strategies run
set saved_strategy_id = saved.id
from public.saved_strategies saved
where saved.source_run_id = run.id
  and run.saved_strategy_id is null;

commit;
