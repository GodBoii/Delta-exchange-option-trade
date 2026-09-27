-- Owner reporting copies. These tables are written by the trading writer in the
-- same transaction as the runtime change they mirror, and nothing in the trading
-- path reads them. A user deleting a run removes trade.* rows only; the ledger
-- row stays with deleted_by_user_at set.
create schema if not exists owner_reporting;

-- Allowed profile fields only. Supabase stays authoritative for identity.
create table if not exists owner_reporting.user_profiles (
    user_id text primary key check (length(user_id) between 1 and 64),
    display_name text,
    email text,
    phone_number text,
    avatar_url text,
    user_type text check (user_type in ('owner', 'user')),
    registered_at timestamptz,
    connection_status text,
    delta_account_name text,
    profile_synced_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists owner_reporting.trade_ledger (
    run_id text primary key,
    owner_user_id text not null references owner_reporting.user_profiles (user_id),
    strategy_name text not null,
    asset text,
    status text not null,
    -- Only 'settled' rows count toward wins, losses and net P&L.
    accounting_state text not null
        check (accounting_state in ('settled','open','scheduled','cancelled','incomplete','attention')),
    exclusion_reason text,
    created_at timestamptz not null,
    entry_at timestamptz,
    exit_at timestamptz,
    entry_executed_at timestamptz,
    exit_executed_at timestamptz,
    activity_at timestamptz not null,
    realized_pnl numeric(28, 10),
    gross_pnl numeric(28, 10),
    exchange_fees numeric(28, 10),
    entry_premium numeric(28, 10),
    exit_premium numeric(28, 10),
    capital_budget numeric(28, 10),
    wallet_total_at_entry numeric(28, 10),
    wallet_available_at_entry numeric(28, 10),
    order_count integer not null default 0 check (order_count >= 0),
    detail jsonb not null check (jsonb_typeof(detail) = 'object'),
    first_captured_at timestamptz not null default now(),
    last_captured_at timestamptz not null default now(),
    deleted_by_user_at timestamptz,
    check (accounting_state <> 'settled' or realized_pnl is not null)
);
create index if not exists trade_ledger_owner_activity_idx
    on owner_reporting.trade_ledger (owner_user_id, activity_at desc, run_id desc);
create index if not exists trade_ledger_visible_activity_idx
    on owner_reporting.trade_ledger (owner_user_id, activity_at desc, run_id desc)
    where deleted_by_user_at is null;
create index if not exists trade_ledger_state_idx
    on owner_reporting.trade_ledger (accounting_state, owner_user_id);

-- Capital values with their source and time. Wallet totals, policy settings and
-- per-run allocations are different quantities and are never merged.
create table if not exists owner_reporting.capital_observations (
    id bigint generated always as identity primary key,
    user_id text not null references owner_reporting.user_profiles (user_id),
    kind text not null check (kind in ('wallet', 'policy', 'run_allocation')),
    source text not null check (source in ('strategy_entry', 'live_wallet', 'capital_policy')),
    observation_key text not null,
    observed_at timestamptz not null,
    run_id text,
    total_balance numeric(28, 10),
    available_balance numeric(28, 10),
    allocation_mode text,
    capital_amount numeric(28, 10),
    allocated_budget numeric(28, 10),
    recorded_at timestamptz not null default now(),
    unique (user_id, kind, observation_key),
    check (kind <> 'wallet' or total_balance is not null),
    check (kind <> 'policy' or allocation_mode is not null),
    check (kind <> 'run_allocation' or (allocated_budget is not null and run_id is not null))
);
create index if not exists capital_observations_user_time_idx
    on owner_reporting.capital_observations (user_id, observed_at desc, id desc);

create table if not exists owner_reporting.automation_changes (
    id bigint generated always as identity primary key,
    actor_user_id text not null,
    target_user_id text not null,
    old_enabled boolean,
    new_enabled boolean not null,
    changed_at timestamptz not null default now()
);
create index if not exists automation_changes_target_idx
    on owner_reporting.automation_changes (target_user_id, changed_at desc);

-- A verified import marks historical totals as complete for this ledger version.
create table if not exists owner_reporting.backfill_runs (
    id bigint generated always as identity primary key,
    ledger_version integer not null,
    started_at timestamptz not null,
    completed_at timestamptz,
    strategies_seen integer not null default 0,
    captured integer not null default 0,
    missing integer,
    mismatched integer,
    verified boolean not null default false,
    error text
);
create index if not exists backfill_runs_version_idx
    on owner_reporting.backfill_runs (ledger_version, verified, completed_at desc);
