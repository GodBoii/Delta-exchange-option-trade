-- Every durable local write and its recovery event share one transaction.
alter table trade.order_intents add column if not exists revision bigint not null default 1;
alter table trade.product_claims add column if not exists revision bigint not null default 1;
alter table trade.exchange_fills add column if not exists revision bigint not null default 1;

create or replace function trade.bump_revision()
returns trigger language plpgsql as $$
begin
    new.revision := old.revision + 1;
    return new;
end;
$$;

create or replace function trade.enqueue_recovery()
returns trigger language plpgsql as $$
declare
    record_data jsonb;
    record_key text;
    record_revision bigint;
begin
    if current_setting('trade.skip_mirror', true) = 'on' then
        return coalesce(new, old);
    end if;
    if tg_op = 'UPDATE' and tg_table_name = 'strategies' then
        if (new.data - 'risk_state' - 'risk_monitor_at' - 'updated_at')
           = (old.data - 'risk_state' - 'risk_monitor_at' - 'updated_at') then
            return new;
        end if;
    end if;
    record_data := case when tg_op = 'DELETE' then to_jsonb(old) else to_jsonb(new) end;
    record_revision := (record_data ->> 'revision')::bigint;
    if tg_op = 'DELETE' then
        record_revision := record_revision + 1;
    end if;
    record_key := case tg_table_name
        when 'users' then record_data ->> 'user_id'
        when 'system_settings' then record_data ->> 'key'
        when 'order_intents' then (record_data ->> 'account_id') || ':' || (record_data ->> 'client_order_id')
        when 'product_claims' then (record_data ->> 'account_id') || ':' || (record_data ->> 'product_id')
        when 'exchange_fills' then (record_data ->> 'account_id') || ':' || (record_data ->> 'fill_id')
        else record_data ->> 'id'
    end;
    insert into trade.recovery_outbox (entity_type,entity_key,revision,operation,payload)
    values (tg_table_name,record_key,record_revision,
            case when tg_op = 'DELETE' then 'delete' else 'upsert' end,
            case when tg_op = 'DELETE' then null else record_data - 'revision' end);
    return coalesce(new, old);
end;
$$;

do $$
declare
    name text;
begin
    foreach name in array array[
        'users','system_settings','saved_strategies','strategies','executions',
        'execution_orders','strategy_capital_slots','strategy_proposals','analysis_jobs',
        'order_intents','product_claims','exchange_fills'
    ] loop
        execute format('drop trigger if exists %I on trade.%I', name || '_revision', name);
        execute format('create trigger %I before update on trade.%I for each row execute function trade.bump_revision()',
                       name || '_revision', name);
        execute format('drop trigger if exists %I on trade.%I', name || '_recovery', name);
        execute format('create trigger %I after insert or update or delete on trade.%I for each row execute function trade.enqueue_recovery()',
                       name || '_recovery', name);
    end loop;
end;
$$;
