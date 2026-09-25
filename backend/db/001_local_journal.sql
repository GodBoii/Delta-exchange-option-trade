-- The local order journal commits an identity before any exchange submission.
-- Keep this schema separate from Supabase Auth and research storage.
create schema if not exists trade;

create table if not exists trade.order_intents (
    account_id text not null,
    client_order_id text not null check (client_order_id ~ '^[a-zA-Z0-9_-]{1,32}$'),
    payload text not null check (length(payload) <= 16384),
    context jsonb,
    strategy_id text generated always as (context ->> 'strategyId') stored,
    materialized boolean not null default false,
    outcome jsonb not null default '{"kind":"unknown"}'::jsonb,
    unresolved boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (account_id, client_order_id),
    check (jsonb_typeof(outcome) = 'object'),
    check (unresolved = (outcome ->> 'kind' = 'unknown'))
);
create index if not exists order_intents_strategy_idx
    on trade.order_intents (account_id, strategy_id, client_order_id);
create index if not exists order_intents_unresolved_idx
    on trade.order_intents (account_id, client_order_id) where unresolved;

create table if not exists trade.product_claims (
    account_id text not null,
    product_id text not null check (product_id ~ '^[1-9][0-9]*$'),
    strategy_id text not null,
    primary key (account_id, product_id)
);
create index if not exists product_claims_strategy_idx
    on trade.product_claims (account_id, strategy_id);

create table if not exists trade.exchange_fills (
    account_id text not null,
    fill_id text not null,
    product_id text not null check (product_id ~ '^[1-9][0-9]*$'),
    order_id text not null,
    side text not null check (side in ('buy', 'sell')),
    quantity numeric not null check (quantity > 0),
    price numeric not null check (price >= 0),
    commission numeric,
    occurred_at timestamptz not null,
    primary key (account_id, fill_id)
);
create index if not exists exchange_fills_product_time_idx
    on trade.exchange_fills (account_id, product_id, occurred_at);
