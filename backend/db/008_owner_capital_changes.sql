-- Owner changes to another account's capital policy. Written in the same
-- transaction as the policy itself; the account holder's own edits are not
-- listed here because they are the default path.
create table if not exists owner_reporting.capital_policy_changes (
    id bigint generated always as identity primary key,
    actor_user_id text not null,
    target_user_id text not null,
    old_allocation_mode text,
    old_capital_amount numeric(28, 10),
    new_allocation_mode text not null,
    new_capital_amount numeric(28, 10),
    changed_at timestamptz not null default now()
);
create index if not exists capital_policy_changes_target_idx
    on owner_reporting.capital_policy_changes (target_user_id, changed_at desc);
