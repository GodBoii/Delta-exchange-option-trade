alter table trade.order_intents add column if not exists recovery_closed_at timestamptz;
alter table trade.exchange_fills add column if not exists recovery_closed_at timestamptz;

create or replace function trade.mark_closed_recovery(p_strategy_id text)
returns void language plpgsql as $$
declare
    closed_at timestamptz := now();
begin
    update trade.executions
    set data = jsonb_set(data, '{recovery_closed_at}', to_jsonb(closed_at::text))
    where relation_id = p_strategy_id and not data ? 'recovery_closed_at';

    update trade.execution_orders
    set data = jsonb_set(data, '{recovery_closed_at}', to_jsonb(closed_at::text))
    where relation_id in (select id from trade.executions where relation_id = p_strategy_id)
      and not data ? 'recovery_closed_at';

    update trade.order_intents
    set recovery_closed_at = closed_at
    where strategy_id = p_strategy_id and recovery_closed_at is null
      and account_id = (
          select 'india:' || (u.connection ->> 'delta_user_id')
          from trade.strategies s join trade.users u on u.user_id = s.owner_id
          where s.id = p_strategy_id
      );

    update trade.exchange_fills
    set recovery_closed_at = closed_at
    where recovery_closed_at is null and account_id = (
        select 'india:' || (u.connection ->> 'delta_user_id')
        from trade.strategies s join trade.users u on u.user_id = s.owner_id
        where s.id = p_strategy_id
    ) and order_id in (
        select data ->> 'delta_order_id' from trade.execution_orders
        where relation_id in (select id from trade.executions where relation_id = p_strategy_id)
          and data ->> 'delta_order_id' is not null
    );
end;
$$;
