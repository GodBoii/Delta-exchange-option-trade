-- Profile reads and privileges are local; Supabase continues to own Auth.
create table if not exists trade.profiles (
    id text primary key,
    data jsonb not null check (data->>'id' = id and data->>'user_type' in ('owner','user')),
    revision bigint not null default 1,
    mirrored_revision bigint not null default 1,
    sync_attempts integer not null default 0,
    next_sync_at timestamptz not null default now(),
    last_sync_error text,
    updated_at timestamptz not null default now()
);
create index if not exists profiles_pending_sync_idx on trade.profiles(next_sync_at)
    where mirrored_revision < revision;
create index if not exists profiles_name_idx on trade.profiles
    ((data->>'user_type'),(data->>'created_at'),id);
create table if not exists trade.profile_sync_state (
    key text primary key,
    value text not null
);

-- Direct local SQL edits participate in the same durable mirror as API edits.
create or replace function trade.queue_profile_mirror()
returns trigger language plpgsql as $$
begin
    new.revision := old.revision + 1;
    new.sync_attempts := 0;
    new.next_sync_at := now();
    new.last_sync_error := null;
    new.updated_at := now();
    return new;
end;
$$;
drop trigger if exists profiles_queue_mirror on trade.profiles;
create trigger profiles_queue_mirror before update of data on trade.profiles
    for each row when (old.data is distinct from new.data) execute function trade.queue_profile_mirror();

-- Durable backoff and escalation survive process restarts. No exposure is cleared here.
create table if not exists trade.recovery_attempts (
    strategy_id text not null,
    operation text not null check (operation in ('entry','reconcile','release')),
    attempts integer not null default 0,
    next_attempt_at timestamptz not null default now(),
    exhausted boolean not null default false,
    last_error_code text,
    updated_at timestamptz not null default now(),
    primary key(strategy_id,operation)
);
