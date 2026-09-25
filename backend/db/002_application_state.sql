-- Application state is authoritative here. The JSON document preserves the
-- existing API wire shape while typed columns make hot lookups indexable.
create table if not exists trade.users (
    user_id text primary key,
    connection jsonb,
    automation jsonb not null,
    capital jsonb not null,
    record jsonb not null,
    revision bigint not null default 1 check (revision > 0)
);
create index if not exists users_automation_enabled_idx
    on trade.users ((automation ->> 'enabled'));
create index if not exists users_delta_account_idx
    on trade.users ((connection ->> 'delta_user_id'));

create table if not exists trade.system_settings (
    key text primary key check (key = 'main'),
    owner_user_id text not null,
    outbound_ip inet,
    ip_checked_at timestamptz,
    analysis jsonb not null,
    revision bigint not null default 1 check (revision > 0)
);

create table if not exists trade.saved_strategies (
    id uuid primary key,
    user_id text,
    name text not null check (length(name) between 2 and 80),
    definition_json jsonb not null,
    enabled_for_ai boolean not null,
    version integer not null check (version > 0),
    source_run_id text,
    deleted boolean not null default false,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    revision bigint not null default 1 check (revision > 0)
);
create index if not exists saved_strategies_owner_active_idx
    on trade.saved_strategies (user_id, name) where not deleted;

create table if not exists trade.runtime_template (
    id text primary key,
    owner_id text not null,
    status text not null,
    relation_id text,
    unique_key text,
    created_at timestamptz not null,
    entry_at timestamptz,
    exit_at timestamptz,
    scheduled_for timestamptz,
    activation_time timestamptz,
    started_at timestamptz,
    data jsonb not null check (jsonb_typeof(data) = 'object'),
    revision bigint not null default 1 check (revision > 0),
    check (data ->> 'id' = id)
);

create table if not exists trade.strategies (like trade.runtime_template including all);
create table if not exists trade.executions (like trade.runtime_template including all);
create table if not exists trade.execution_orders (like trade.runtime_template including all);
create table if not exists trade.strategy_capital_slots (like trade.runtime_template including all);
create table if not exists trade.strategy_proposals (like trade.runtime_template including all);
create table if not exists trade.analysis_jobs (like trade.runtime_template including all);

alter table trade.strategies drop constraint if exists strategies_status_check;
alter table trade.strategies add constraint strategies_status_check
    check (status in ('draft','scheduled','executing_entry','active','executing_exit','completed','attention','cancelled'));
alter table trade.executions drop constraint if exists executions_status_check;
alter table trade.executions add constraint executions_status_check
    check (status in ('running','completed','partial_or_failed'));
alter table trade.strategy_capital_slots drop constraint if exists capital_slots_status_check;
alter table trade.strategy_capital_slots add constraint capital_slots_status_check
    check (status in ('available','reserved','active'));
alter table trade.analysis_jobs drop constraint if exists analysis_jobs_status_check;
alter table trade.analysis_jobs add constraint analysis_jobs_status_check
    check (status in ('scheduled','running','completed','failed','cancelled'));

create unique index if not exists execution_orders_client_id_idx
    on trade.execution_orders (unique_key) where unique_key is not null;
create unique index if not exists capital_slots_number_idx
    on trade.strategy_capital_slots (owner_id, unique_key) where unique_key is not null;
create unique index if not exists analysis_jobs_run_key_idx
    on trade.analysis_jobs (owner_id, unique_key) where unique_key is not null;
create unique index if not exists proposals_shared_decision_idx
    on trade.strategy_proposals (owner_id, unique_key) where unique_key is not null;

create index if not exists strategies_due_entry_idx
    on trade.strategies (entry_at, id) where status = 'scheduled';
create index if not exists strategies_due_exit_idx
    on trade.strategies (exit_at, id) where status = 'active';
create index if not exists strategies_active_idx
    on trade.strategies (owner_id, id) where status = 'active';
create index if not exists strategies_owner_created_idx
    on trade.strategies (owner_id, created_at desc);
create index if not exists analysis_jobs_due_idx
    on trade.analysis_jobs (scheduled_for, id) where status = 'scheduled';
create index if not exists analysis_jobs_running_idx
    on trade.analysis_jobs (started_at) where status = 'running';
create index if not exists analysis_jobs_owner_created_idx
    on trade.analysis_jobs (owner_id, created_at desc);
create index if not exists proposals_due_idx
    on trade.strategy_proposals (activation_time, id) where status = 'scheduled';

create index if not exists executions_strategy_idx on trade.executions (relation_id, created_at desc);
create index if not exists orders_execution_idx on trade.execution_orders (relation_id, created_at desc);
create index if not exists slots_strategy_idx on trade.strategy_capital_slots (relation_id);
create index if not exists proposals_strategy_idx on trade.strategy_proposals (relation_id);
create index if not exists jobs_proposal_idx on trade.analysis_jobs (relation_id);

create table if not exists trade.recovery_outbox (
    id bigint generated always as identity primary key,
    entity_type text not null,
    entity_key text not null,
    revision bigint not null,
    operation text not null check (operation in ('upsert','delete')),
    payload jsonb,
    created_at timestamptz not null default now(),
    delivered_at timestamptz
);
create index if not exists recovery_outbox_pending_idx
    on trade.recovery_outbox (id) where delivered_at is null;
