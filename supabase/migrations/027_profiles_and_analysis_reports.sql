-- Additive first. Trading-table removal is a separate, verified cutover step.
begin;
alter table public.profiles add column if not exists email text;
alter table public.profiles add column if not exists user_type text not null default 'user'
  check (user_type in ('owner', 'user'));
update public.profiles p set email = u.email from auth.users u where u.id = p.id;
update public.profiles set user_type = case
  when id = '7eebbd1c-ca25-4701-9687-43b918cccea0' then 'owner' else 'user' end;
create unique index if not exists profiles_one_owner on public.profiles (user_type) where user_type = 'owner';

create or replace function public.sync_auth_profile()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, email, display_name, phone_number, avatar_url)
  values (new.id, new.email,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name'),
    case when new.raw_user_meta_data->>'phone_number' ~ '^\+[1-9][0-9]{7,14}$'
      then new.raw_user_meta_data->>'phone_number' else null end,
    new.raw_user_meta_data->>'avatar_url')
  on conflict (id) do update set email = excluded.email, updated_at = now();
  return new;
end;
$$;
drop trigger if exists sync_auth_profile on auth.users;
drop trigger if exists on_auth_user_created on auth.users;
create trigger sync_auth_profile after insert or update of email on auth.users
  for each row execute function public.sync_auth_profile();

create table if not exists public.analysis_reports (
  id uuid primary key,
  snapshot_id uuid,
  legacy_user_id uuid,
  report_markdown text,
  member_responses jsonb not null default '[]',
  tool_calls jsonb not null default '[]',
  market_json jsonb not null default '{}',
  account_json jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on column public.analysis_reports.legacy_user_id is
  'Only historical private analyses have an identity. New global reports use NULL.';
alter table public.analysis_reports enable row level security;
revoke all on public.analysis_reports from anon, authenticated;
grant all on public.analysis_reports to service_role;
revoke insert, update, delete on public.profiles from anon, authenticated;
create index if not exists analysis_reports_snapshot on public.analysis_reports (snapshot_id);
create index if not exists analysis_reports_history on public.analysis_reports (created_at desc);
commit;
