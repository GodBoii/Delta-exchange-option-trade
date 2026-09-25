create table if not exists trade.recovery_gate (
    key text primary key check (key = 'main'),
    pending boolean not null default false,
    reason text,
    updated_at timestamptz not null default now()
);
insert into trade.recovery_gate (key,pending) values ('main',false)
on conflict (key) do nothing;
