-- Account-wide capital allocation. Strategy definitions no longer own capital percentages.
begin;

create table if not exists public.capital_settings (
  user_id uuid primary key references auth.users(id) on delete cascade,
  allocation_mode text not null default 'half_balance' check (
    allocation_mode in (
      'full_balance', 'half_balance', 'one_third_balance', 'one_quarter_balance', 'fixed_amount'
    )
  ),
  capital_amount numeric check (capital_amount is null or capital_amount > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    (allocation_mode = 'fixed_amount' and capital_amount is not null)
    or (allocation_mode <> 'fixed_amount' and capital_amount is null)
  )
);

comment on table public.capital_settings is
  'One account-level capital budget used by every manual and automated strategy entry.';

drop trigger if exists capital_settings_updated_at on public.capital_settings;
create trigger capital_settings_updated_at
before update on public.capital_settings
for each row execute function public.set_updated_at();

alter table public.capital_settings enable row level security;

drop policy if exists capital_settings_select_own on public.capital_settings;
create policy capital_settings_select_own on public.capital_settings for select to authenticated
using ((select auth.uid()) = user_id);
drop policy if exists capital_settings_insert_own on public.capital_settings;
create policy capital_settings_insert_own on public.capital_settings for insert to authenticated
with check ((select auth.uid()) = user_id);
drop policy if exists capital_settings_update_own on public.capital_settings;
create policy capital_settings_update_own on public.capital_settings for update to authenticated
using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

grant select, insert, update on public.capital_settings to authenticated;
grant all on public.capital_settings to service_role;

insert into public.capital_settings (user_id, allocation_mode)
select id, 'half_balance' from auth.users
on conflict (user_id) do nothing;

-- Remove retired per-strategy capital fields from reusable definitions and immutable runs.
update public.saved_strategies
set definition_json = definition_json - 'allocationMode' - 'capitalAmount';

update public.strategies
set definition_json = definition_json - 'allocationMode' - 'capitalAmount';

create or replace function public.strip_strategy_capital_fields()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.definition_json = new.definition_json - 'allocationMode' - 'capitalAmount';
  return new;
end;
$$;

drop trigger if exists saved_strategies_account_capital on public.saved_strategies;
create trigger saved_strategies_account_capital
before insert or update on public.saved_strategies
for each row execute function public.strip_strategy_capital_fields();

drop trigger if exists strategies_account_capital on public.strategies;
create trigger strategies_account_capital
before insert or update on public.strategies
for each row execute function public.strip_strategy_capital_fields();

alter table public.strategies
  add column if not exists capital_slot smallint,
  add column if not exists capital_budget numeric,
  add column if not exists capital_policy_json jsonb not null default '{}'::jsonb;

alter table public.strategy_capital_slots
  drop constraint if exists strategy_capital_slots_slot_number_check;
alter table public.strategy_capital_slots
  add constraint strategy_capital_slots_slot_number_check check (slot_number between 1 and 100);

drop function if exists public.reserve_strategy_capital_slot(uuid, uuid);

create function public.reserve_strategy_capital_slot(
  p_user_id uuid,
  p_strategy_id uuid,
  p_maximum_slots smallint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_slot smallint;
  v_occupied_before integer;
begin
  if p_maximum_slots < 1 or p_maximum_slots > 100 then
    raise exception 'maximum slots must be between 1 and 100';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

  select slot_number into v_slot
  from public.strategy_capital_slots
  where user_id = p_user_id and strategy_id = p_strategy_id and status in ('reserved', 'active')
  limit 1;
  if v_slot is not null then
    select count(*) - 1 into v_occupied_before
    from public.strategy_capital_slots
    where user_id = p_user_id and status in ('reserved', 'active');
    return jsonb_build_object(
      'slot', v_slot,
      'created', false,
      'occupiedBefore', greatest(v_occupied_before, 0)
    );
  end if;

  select count(*) into v_occupied_before
  from public.strategy_capital_slots
  where user_id = p_user_id and status in ('reserved', 'active');
  if v_occupied_before >= p_maximum_slots then
    return null;
  end if;

  update public.strategy_capital_slots
  set status = 'reserved', strategy_id = p_strategy_id, proposal_id = null,
      reserved_at = now(), released_at = null
  where id = (
    select id from public.strategy_capital_slots
    where user_id = p_user_id and status = 'available' and slot_number <= p_maximum_slots
    order by slot_number
    limit 1
    for update
  )
  returning slot_number into v_slot;

  if v_slot is null then
    select candidate into v_slot
    from generate_series(1, p_maximum_slots) as candidate
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
  end if;

  return jsonb_build_object(
    'slot', v_slot,
    'created', true,
    'occupiedBefore', v_occupied_before
  );
end;
$$;

revoke all on function public.reserve_strategy_capital_slot(uuid, uuid, smallint)
  from public, anon, authenticated;
grant execute on function public.reserve_strategy_capital_slot(uuid, uuid, smallint) to service_role;

commit;
