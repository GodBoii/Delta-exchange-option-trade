-- Update saved shared templates only. Existing scheduled and historical runs retain their snapshots.
begin;

update public.saved_strategies saved
set definition_json = jsonb_set(
  jsonb_set(saved.definition_json, '{takeProfitPercent}', '80'::jsonb, true),
  '{legs}',
  (
    select jsonb_agg((leg - 'limitPrice') || '{"orderType":"market_order"}'::jsonb order by ordinal)
    from jsonb_array_elements(saved.definition_json -> 'legs') with ordinality as item(leg, ordinal)
  ),
  true
)
where saved.user_id is null and saved.is_default = true
  and (
    saved.definition_json ->> 'takeProfitPercent' is distinct from '80'
    or exists (
      select 1 from jsonb_array_elements(saved.definition_json -> 'legs') leg
      where leg ->> 'orderType' is distinct from 'market_order' or leg ? 'limitPrice'
    )
  );

commit;
