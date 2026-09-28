-- Browser push subscriptions for the installed app. One row per device; the
-- endpoint is the push service URL the browser handed out, so it is unique.
create table if not exists trade.push_subscriptions (
    endpoint text primary key check (endpoint like 'https://%' and length(endpoint) <= 2048),
    user_id text not null check (length(user_id) between 1 and 64),
    p256dh text not null check (length(p256dh) between 1 and 200),
    auth text not null check (length(auth) between 1 and 100),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists push_subscriptions_user_idx on trade.push_subscriptions (user_id);
