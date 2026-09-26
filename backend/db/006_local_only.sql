-- Local PostgreSQL is the only application database. The remote recovery
-- copy is retired, so its outbox, restore gate and retention markers go away.
do $$
declare
    name text;
begin
    foreach name in array array[
        'users','system_settings','saved_strategies','strategies','executions',
        'execution_orders','strategy_capital_slots','strategy_proposals','analysis_jobs',
        'order_intents','product_claims','exchange_fills'
    ] loop
        execute format('drop trigger if exists %I on trade.%I', name || '_recovery', name);
    end loop;
end;
$$;
drop function if exists trade.enqueue_recovery();
drop function if exists trade.mark_closed_recovery(text);
drop table if exists trade.recovery_outbox;
drop table if exists trade.recovery_gate;
alter table trade.order_intents drop column if exists recovery_closed_at;
alter table trade.exchange_fills drop column if exists recovery_closed_at;
update trade.executions set data = data - 'recovery_closed_at' where data ? 'recovery_closed_at';
update trade.execution_orders set data = data - 'recovery_closed_at' where data ? 'recovery_closed_at';

-- Browsers refetch when a strategy or analysis job changes. NOTIFY is delivered
-- on commit to every listening API process, so any number of replicas share
-- one change feed. Risk-display refreshes do not wake clients.
create or replace function trade.notify_change()
returns trigger language plpgsql as $$
begin
    if tg_op = 'UPDATE' and tg_table_name = 'strategies'
       and (new.data - 'risk_state' - 'risk_monitor_at' - 'updated_at')
           = (old.data - 'risk_state' - 'risk_monitor_at' - 'updated_at') then
        return null;
    end if;
    perform pg_notify(
        'trade_changes',
        json_build_object(
            'table', tg_table_name,
            'owner', case when tg_op = 'DELETE' then old.owner_id else new.owner_id end
        )::text
    );
    return null;
end;
$$;

drop trigger if exists strategies_notify on trade.strategies;
create trigger strategies_notify after insert or update or delete on trade.strategies
    for each row execute function trade.notify_change();
drop trigger if exists analysis_jobs_notify on trade.analysis_jobs;
create trigger analysis_jobs_notify after insert or update or delete on trade.analysis_jobs
    for each row execute function trade.notify_change();

-- Research artifacts. The analysis service writes here with its own role;
-- Agno creates its session tables in the same schema.
create schema if not exists ai;

create table if not exists ai.analysis_reports (
    id uuid primary key,
    snapshot_id uuid,
    report_markdown text,
    member_responses jsonb not null default '[]'::jsonb,
    tool_calls jsonb not null default '[]'::jsonb,
    market_json jsonb not null default '{}'::jsonb,
    account_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists analysis_reports_snapshot_idx on ai.analysis_reports (snapshot_id);
create index if not exists analysis_reports_created_idx on ai.analysis_reports (created_at desc);

create table if not exists ai.chart_images (
    run_id uuid not null,
    chart_id text not null check (length(chart_id) between 1 and 100),
    owner_id text not null,
    content_type text not null default 'image/png' check (content_type = 'image/png'),
    content bytea not null check (octet_length(content) between 1 and 5242880),
    created_at timestamptz not null default now(),
    primary key (run_id, chart_id)
);
create index if not exists chart_images_created_idx on ai.chart_images (created_at);
