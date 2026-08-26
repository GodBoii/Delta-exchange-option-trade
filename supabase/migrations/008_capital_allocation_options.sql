-- Configurable per-strategy capital caps. Existing saved strategies default to full available margin.
begin;

update public.saved_strategies
set definition_json = jsonb_set(
  definition_json - 'capitalAmount' - 'maximumLots',
  '{allocationMode}',
  '"full_balance"'::jsonb,
  true
);

commit;
