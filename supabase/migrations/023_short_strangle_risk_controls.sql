-- Update the existing shared Short strangle without creating a renamed copy.
-- Historical strategy runs keep their immutable definition snapshots.
begin;

do $$
declare
  affected integer;
begin
  update public.saved_strategies
  set definition_json = jsonb_set(
    jsonb_set(definition_json, '{takeProfitPercent}', '90'::jsonb, true),
    '{emergencyStopLossPercent}',
    '170'::jsonb,
    true
  )
  where id = '10000000-0000-4000-8000-000000000006'
    and user_id is null
    and is_default = true
    and name = 'Short strangle'
    and definition_json ->> 'name' = 'Short strangle';

  get diagnostics affected = row_count;
  if affected <> 1 then
    raise exception 'Expected one shared Short strangle; updated %', affected;
  end if;

  if not exists (
    select 1
    from public.saved_strategies
    where id = '10000000-0000-4000-8000-000000000006'
      and name = 'Short strangle'
      and definition_json ->> 'name' = 'Short strangle'
      and (definition_json ->> 'takeProfitPercent')::numeric = 90
      and (definition_json ->> 'stopLossPercent')::numeric = 100
      and (definition_json ->> 'combinedStopLossPercent')::numeric = 100
      and (definition_json ->> 'emergencyStopLossPercent')::numeric = 170
  ) then
    raise exception 'Short strangle risk controls failed verification';
  end if;
end;
$$;

commit;
