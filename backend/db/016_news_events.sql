-- Public research only. No account data or trading permissions are required.
create table if not exists ai.news_events (
    event_id text primary key,
    payload jsonb not null check (jsonb_typeof(payload) = 'object'),
    first_seen_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists news_events_updated_idx on ai.news_events(updated_at desc);
