-- Live-only automation. The agent schedules the existing strategy engine and has no separate mode.
begin;

drop policy if exists automation_settings_insert_own on public.automation_settings;
drop policy if exists automation_settings_update_own on public.automation_settings;

alter table public.automation_settings
  drop column if exists mode,
  drop column if exists asia_review,
  drop column if exists london_review,
  drop column if exists new_york_review;

alter table public.automation_agent_runs
  add column if not exists agno_session_id text,
  add column if not exists agno_run_id text;

alter table public.strategy_proposals
  add column if not exists strategy_id uuid references public.strategies(id) on delete set null;

update public.saved_strategies
set definition_json = case
  when definition_json ->> 'lotsMode' = 'auto'
    and coalesce(definition_json ->> 'maximumLots', '') = ''
  then jsonb_set(definition_json - 'selectionCriteria', '{maximumLots}', '1'::jsonb, true)
  else definition_json - 'selectionCriteria'
end;

drop policy if exists automation_settings_insert_own on public.automation_settings;
create policy automation_settings_insert_own on public.automation_settings for insert to authenticated
with check ((select auth.uid()) = user_id);
drop policy if exists automation_settings_update_own on public.automation_settings;
create policy automation_settings_update_own on public.automation_settings for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

create or replace function public.reserve_strategy_capital_slot(
  p_user_id uuid,
  p_strategy_id uuid
)
returns smallint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_slot smallint;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

  select slot_number into v_slot
  from public.strategy_capital_slots
  where user_id = p_user_id and strategy_id = p_strategy_id and status in ('reserved', 'active')
  limit 1;
  if v_slot is not null then return v_slot; end if;

  update public.strategy_capital_slots
  set status = 'reserved', strategy_id = p_strategy_id, proposal_id = null,
      reserved_at = now(), released_at = null
  where id = (
    select id from public.strategy_capital_slots
    where user_id = p_user_id and status = 'available'
    order by slot_number
    limit 1
    for update
  )
  returning slot_number into v_slot;
  if v_slot is not null then return v_slot; end if;

  select candidate into v_slot
  from generate_series(1, 3) as candidate
  where not exists (
    select 1 from public.strategy_capital_slots
    where user_id = p_user_id and slot_number = candidate
  )
  order by candidate
  limit 1;
  if v_slot is null then return null; end if;

  insert into public.strategy_capital_slots (
    user_id, slot_number, strategy_id, status, reserved_at
  ) values (
    p_user_id, v_slot, p_strategy_id, 'reserved', now()
  );
  return v_slot;
end;
$$;

create or replace function public.release_strategy_capital_slot(
  p_user_id uuid,
  p_strategy_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.strategy_capital_slots
  set status = 'available', strategy_id = null, proposal_id = null,
      released_at = now(), reserved_at = null
  where user_id = p_user_id and strategy_id = p_strategy_id;
  return found;
end;
$$;

revoke all on function public.reserve_strategy_capital_slot(uuid, uuid) from public, anon, authenticated;
revoke all on function public.release_strategy_capital_slot(uuid, uuid) from public, anon, authenticated;
grant execute on function public.reserve_strategy_capital_slot(uuid, uuid) to service_role;
grant execute on function public.release_strategy_capital_slot(uuid, uuid) to service_role;

commit;
