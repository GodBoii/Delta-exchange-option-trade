-- Additive account management. Existing connections and runs keep their identity.
create table if not exists trade.account_management (
    owner_id text primary key references trade.users(user_id),
    mode text not null default 'main' check (mode in ('main','automatic','manual')),
    auto_fund boolean not null default false,
    revision bigint not null default 1,
    updated_at timestamptz not null default now()
);

create table if not exists trade.exchange_accounts (
    owner_id text not null references trade.users(user_id),
    account_id text not null,
    parent_id text not null,
    account_name text not null,
    margin_mode text,
    present boolean not null default true,
    participation text not null default 'disabled' check (participation in ('disabled','manual','automatic')),
    credential jsonb,
    checked_at timestamptz,
    discovered_at timestamptz not null default now(),
    primary key (owner_id,account_id),
    check (account_id <> parent_id)
);
create index if not exists exchange_accounts_parent_idx on trade.exchange_accounts(parent_id,account_id);

create table if not exists trade.account_assignments (
    strategy_id text primary key references trade.strategies(id) on delete cascade,
    owner_id text not null,
    account_id text not null,
    parent_id text not null,
    budget numeric(28,10) check (budget > 0),
    pool_total numeric(28,10) check (pool_total > 0),
    state text not null default 'reserved' check (state in ('reserved','active','released')),
    created_at timestamptz not null default now(),
    released_at timestamptz,
    foreign key (owner_id,account_id) references trade.exchange_accounts(owner_id,account_id)
);
-- Includes aliases signed into the same exchange account through another login.
create unique index if not exists account_assignments_busy_idx
    on trade.account_assignments(account_id) where state <> 'released';
create index if not exists account_assignments_owner_idx on trade.account_assignments(owner_id,state);

create table if not exists trade.account_transfers (
    id uuid primary key,
    owner_id text not null references trade.users(user_id),
    parent_id text not null,
    source_id text not null,
    target_id text not null,
    asset_symbol text not null check (asset_symbol = 'USD'),
    amount numeric(28,10) not null check (amount > 0),
    state text not null check (state in ('prepared','submitting','confirmed','unknown','failed')),
    strategy_id text,
    error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (source_id <> target_id)
);
-- A timed-out transfer blocks subsequent family transfers until reconciled.
create unique index if not exists account_transfers_pending_idx on trade.account_transfers(parent_id)
    where state in ('prepared','submitting','unknown');
create index if not exists account_transfers_owner_idx on trade.account_transfers(owner_id,created_at desc);

create or replace function trade.keep_assignment_identity() returns trigger language plpgsql as $$
begin
    if (new.strategy_id,new.owner_id,new.account_id,new.parent_id)
       is distinct from (old.strategy_id,old.owner_id,old.account_id,old.parent_id) then
        raise exception 'A strategy exchange account cannot change';
    end if;
    if old.budget is not null and (new.budget,new.pool_total) is distinct from (old.budget,old.pool_total) then
        raise exception 'An assigned capital budget cannot change';
    end if;
    return new;
end;
$$;
drop trigger if exists keep_assignment_identity on trade.account_assignments;
create trigger keep_assignment_identity before update on trade.account_assignments
    for each row execute function trade.keep_assignment_identity();
