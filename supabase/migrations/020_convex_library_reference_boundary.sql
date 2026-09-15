-- MANUAL CUTOVER ONLY. First export/import/verify saved strategies and capital
-- settings in Convex, pause scheduling and library writes, and deploy all readers.
-- IDs and definition snapshots remain on runs; Convex owns the reusable library.
-- Do not delete the old tables. Native Agno session storage is unchanged.
begin;

alter table public.strategies
  drop constraint if exists strategies_saved_strategy_id_fkey;
alter table public.strategy_proposals
  drop constraint if exists strategy_proposals_saved_strategy_id_fkey;

-- Browser library writes move to authenticated Convex mutations after cutover.
revoke insert, update, delete on public.saved_strategies from anon, authenticated;

commit;

-- Verify both removed links are absent. Existing execution/proposal history stays.
select conname from pg_constraint
where conrelid in ('public.strategies'::regclass, 'public.strategy_proposals'::regclass)
  and conname in ('strategies_saved_strategy_id_fkey', 'strategy_proposals_saved_strategy_id_fkey');
