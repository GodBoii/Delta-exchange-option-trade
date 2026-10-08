-- Unplaced entries are history, not unresolved exposure alerts.
alter table trade.strategies drop constraint strategies_status_check;
alter table trade.strategies add constraint strategies_status_check
    check (status in ('draft','scheduled','executing_entry','active','executing_exit',
                     'completed','attention','cancelled','skipped'));

-- Reclassify only explicit pre-entry rejections with no execution, journal intent,
-- product claim or capital reservation. Keep every original reason and timestamp.
with unplaced as (
    select s.id,
           case when s.data->>'last_error' = 'Entry not placed: Account capital is already reserved'
                then 'capital_reserved' else 'legacy_entry_rejection' end as code,
           case when s.data->>'last_error' = 'Entry not placed: Account capital is already reserved'
                then 'capital_full' else 'unclassified' end as category
    from trade.strategies s
    where s.status='attention'
      and s.data->>'entry_execution_at' is null
      and s.data->>'exit_execution_at' is null
      and s.data->>'last_error' like 'Entry not placed:%'
      and not exists (select 1 from trade.executions e where e.relation_id=s.id)
      and not exists (select 1 from trade.order_intents i where i.strategy_id=s.id)
      and not exists (select 1 from trade.product_claims p where p.strategy_id=s.id)
      and not exists (select 1 from trade.strategy_capital_slots c where c.relation_id=s.id)
)
update trade.strategies s set status='skipped', revision=s.revision+1,
    data=s.data || jsonb_build_object('status','skipped','entry_outcome',jsonb_build_object(
        'status','skipped','code',u.code,'category',u.category,
        'message',substring(s.data->>'last_error' from 19),
        'occurredAt',coalesce(s.data->>'updated_at',s.created_at::text),
        'previousStatus','attention'))
from unplaced u where s.id=u.id;
