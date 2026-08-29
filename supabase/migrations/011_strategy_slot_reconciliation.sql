-- Capital slots follow live exposure and cannot remain attached to terminal runs.
begin;

create index if not exists strategy_capital_slots_strategy_idx
  on public.strategy_capital_slots(strategy_id)
  where strategy_id is not null;

create or replace function public.release_terminal_strategy_capital_slot()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_strategy_id uuid;
begin
  if tg_op = 'DELETE' then
    v_strategy_id = old.id;
  else
    v_strategy_id = new.id;
  end if;
  update public.strategy_capital_slots
  set status = 'available', strategy_id = null, proposal_id = null,
      released_at = now(), reserved_at = null
  where strategy_id = v_strategy_id;
  if tg_op = 'DELETE' then return old; end if;
  return new;
end;
$$;

drop trigger if exists strategies_release_terminal_slot on public.strategies;
create trigger strategies_release_terminal_slot
after update of status on public.strategies
for each row
when (new.status in ('completed', 'cancelled') and old.status is distinct from new.status)
execute function public.release_terminal_strategy_capital_slot();

drop trigger if exists strategies_release_slot_before_delete on public.strategies;
create trigger strategies_release_slot_before_delete
before delete on public.strategies
for each row execute function public.release_terminal_strategy_capital_slot();

-- Repair slots left behind by runs that became terminal before this trigger existed.
update public.strategy_capital_slots slot
set status = 'available', strategy_id = null, proposal_id = null,
    released_at = now(), reserved_at = null
where slot.strategy_id is not null
  and exists (
    select 1 from public.strategies strategy
    where strategy.id = slot.strategy_id
      and strategy.status in ('completed', 'cancelled')
  );

revoke all on function public.release_terminal_strategy_capital_slot() from public, anon, authenticated;

commit;
