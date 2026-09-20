-- Apply the shared risk policy without renaming or duplicating strategies.
-- Historical strategy runs keep their immutable definition snapshots.
begin;

do $$
declare
  affected integer;
  default_count integer;
begin
  if exists (
    select 1
    from public.saved_strategies
    where id = '10000000-0000-4000-8000-000000000007'
      and (user_id is not null or is_default = false or name <> 'Iron condor')
  ) or exists (
    select 1
    from public.saved_strategies
    where id = '10000000-0000-4000-8000-000000000008'
      and (user_id is not null or is_default = false or name <> 'Iron butterfly')
  ) then
    raise exception 'Retired strategy identity does not match the expected shared default';
  end if;

  if exists (
    select 1
    from public.strategies
    where saved_strategy_id in (
      '10000000-0000-4000-8000-000000000007',
      '10000000-0000-4000-8000-000000000008'
    )
      and status in ('draft', 'scheduled', 'active', 'executing_entry', 'executing_exit', 'attention')
  ) then
    raise exception 'Cannot retire an iron strategy while a nonterminal run references it';
  end if;

  delete from public.saved_strategies
  where id in (
    '10000000-0000-4000-8000-000000000007',
    '10000000-0000-4000-8000-000000000008'
  )
    and user_id is null
    and is_default = true;

  select count(*) into default_count
  from public.saved_strategies
  where user_id is null and is_default = true;

  if default_count < 6 then
    raise exception 'Expected at least six shared strategies after retirement; found %', default_count;
  end if;

  update public.saved_strategies saved
  set definition_json = jsonb_set(
    case
      when exists (
        select 1
        from jsonb_array_elements(saved.definition_json -> 'legs') leg
        where leg ->> 'position' = 'sell'
      ) then jsonb_set(saved.definition_json, '{emergencyStopLossPercent}', '170'::jsonb, true)
      else saved.definition_json
    end,
    '{takeProfitPercent}',
    '90'::jsonb,
    true
  )
  where saved.user_id is null and saved.is_default = true;

  get diagnostics affected = row_count;
  if affected <> default_count then
    raise exception 'Expected to update % shared strategies; updated %', default_count, affected;
  end if;

  if exists (
    select 1
    from public.saved_strategies saved
    where saved.user_id is null
      and saved.is_default = true
      and (saved.definition_json ->> 'takeProfitPercent')::numeric <> 90
  ) then
    raise exception 'A shared strategy does not use the 90%% take-profit target';
  end if;

  if exists (
    select 1
    from public.saved_strategies saved
    where saved.user_id is null
      and saved.is_default = true
      and exists (
        select 1
        from jsonb_array_elements(saved.definition_json -> 'legs') leg
        where leg ->> 'position' = 'sell'
      )
      and (saved.definition_json ->> 'emergencyStopLossPercent')::numeric <> 170
  ) then
    raise exception 'A shared short-leg strategy does not use the 170%% emergency stop';
  end if;

  if exists (
    select 1
    from public.saved_strategies saved
    where saved.user_id is null
      and saved.is_default = true
      and saved.definition_json ->> 'riskMode' = 'combined_premium'
      and (saved.definition_json ->> 'combinedStopLossPercent')::numeric <> 100
  ) then
    raise exception 'A combined-premium strategy does not use the 100%% combined stop';
  end if;
end;
$$;

commit;
