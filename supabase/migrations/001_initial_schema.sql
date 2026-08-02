-- Delta Strategy Desk: Supabase Auth, Vault-backed Delta connections, and trading records.
-- Run this entire file once in Supabase Dashboard > SQL Editor as the project owner.

begin;

create extension if not exists pgcrypto with schema extensions;
create extension if not exists supabase_vault;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  avatar_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.exchange_connections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references auth.users(id) on delete cascade,
  provider text not null default 'delta_india' check (provider = 'delta_india'),
  api_key text not null,
  vault_secret_id uuid not null unique,
  environment text not null default 'production' check (environment = 'production'),
  delta_user_id text,
  account_name text,
  email_masked text,
  status text not null default 'connected' check (status in ('connected', 'invalid', 'revoked')),
  verified_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.strategies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 2 and 80),
  status text not null check (status in ('draft','scheduled','executing_entry','active','executing_exit','completed','attention','cancelled')),
  definition_json jsonb not null,
  entry_at timestamptz,
  exit_at timestamptz,
  entry_execution_at timestamptz,
  exit_execution_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.executions (
  id uuid primary key default gen_random_uuid(),
  strategy_id uuid not null references public.strategies(id) on delete cascade,
  kind text not null check (kind in ('entry','exit')),
  status text not null check (status in ('running','completed','partial_or_failed')),
  error text,
  started_at timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists public.execution_orders (
  id uuid primary key default gen_random_uuid(),
  execution_id uuid not null references public.executions(id) on delete cascade,
  leg_id text not null,
  delta_order_id text,
  client_order_id text not null unique check (char_length(client_order_id) <= 32),
  product_id bigint not null,
  product_symbol text not null,
  side text not null check (side in ('buy','sell')),
  size integer not null check (size > 0),
  state text not null,
  response_json jsonb,
  created_at timestamptz not null default now()
);

create index if not exists strategies_user_created_idx on public.strategies(user_id, created_at desc);
create index if not exists strategies_due_entry_idx on public.strategies(entry_at) where status = 'scheduled' and entry_execution_at is null;
create index if not exists strategies_due_exit_idx on public.strategies(exit_at) where status = 'active' and exit_execution_at is null;
create index if not exists executions_strategy_idx on public.executions(strategy_id, started_at desc);
create index if not exists execution_orders_execution_idx on public.execution_orders(execution_id);

drop trigger if exists profiles_updated_at on public.profiles;
create trigger profiles_updated_at before update on public.profiles for each row execute function public.set_updated_at();
drop trigger if exists exchange_connections_updated_at on public.exchange_connections;
create trigger exchange_connections_updated_at before update on public.exchange_connections for each row execute function public.set_updated_at();
drop trigger if exists strategies_updated_at on public.strategies;
create trigger strategies_updated_at before update on public.strategies for each row execute function public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name, avatar_url)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name', split_part(new.email, '@', 1)),
    new.raw_user_meta_data ->> 'avatar_url'
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();

-- Server-only RPC: atomically creates/rotates the per-user Delta secret in Vault.
create or replace function public.store_delta_connection(
  p_user_id uuid,
  p_api_key text,
  p_api_secret text,
  p_delta_user_id text,
  p_account_name text,
  p_email_masked text
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_connection_id uuid;
  v_secret_id uuid;
begin
  if p_user_id is null or nullif(trim(p_api_key), '') is null or nullif(trim(p_api_secret), '') is null then
    raise exception 'user id, api key, and api secret are required';
  end if;

  select vault_secret_id into v_secret_id
  from public.exchange_connections
  where user_id = p_user_id
  for update;

  if v_secret_id is null then
    select vault.create_secret(
      p_api_secret,
      'delta_connection_' || p_user_id::text,
      'Delta Exchange India API secret for one application user'
    ) into v_secret_id;
  else
    perform vault.update_secret(v_secret_id, p_api_secret);
  end if;

  insert into public.exchange_connections (
    user_id, api_key, vault_secret_id, environment, delta_user_id, account_name, email_masked, status, verified_at
  ) values (
    p_user_id, p_api_key, v_secret_id, 'production', p_delta_user_id, p_account_name, p_email_masked, 'connected', now()
  )
  on conflict (user_id) do update set
    api_key = excluded.api_key,
    vault_secret_id = excluded.vault_secret_id,
    delta_user_id = excluded.delta_user_id,
    account_name = excluded.account_name,
    email_masked = excluded.email_masked,
    status = 'connected',
    verified_at = now(),
    updated_at = now()
  returning id into v_connection_id;

  return v_connection_id;
end;
$$;

-- Server-only RPC: decrypts credentials only for the trusted signing backend.
create or replace function public.get_delta_credentials(p_user_id uuid)
returns table (
  connection_id uuid,
  api_key text,
  api_secret text,
  environment text,
  delta_user_id text,
  account_name text,
  email_masked text,
  status text
)
language sql
security definer
set search_path = ''
stable
as $$
  select
    c.id,
    c.api_key,
    s.decrypted_secret,
    c.environment,
    c.delta_user_id,
    c.account_name,
    c.email_masked,
    c.status
  from public.exchange_connections c
  join vault.decrypted_secrets s on s.id = c.vault_secret_id
  where c.user_id = p_user_id
  limit 1;
$$;

-- Server-only RPC: disconnects Delta and deletes its Vault secret.
create or replace function public.delete_delta_connection(p_user_id uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_secret_id uuid;
begin
  delete from public.exchange_connections
  where user_id = p_user_id
  returning vault_secret_id into v_secret_id;
  if v_secret_id is null then return false; end if;
  delete from vault.secrets where id = v_secret_id;
  return true;
end;
$$;

alter table public.profiles enable row level security;
alter table public.exchange_connections enable row level security;
alter table public.strategies enable row level security;
alter table public.executions enable row level security;
alter table public.execution_orders enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles for select to authenticated using ((select auth.uid()) = id);
drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own" on public.profiles for update to authenticated using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

-- Connections and Vault material are backend-only; the UI reads a sanitized API response.
revoke all on table public.exchange_connections from anon, authenticated;

drop policy if exists "strategies_select_own" on public.strategies;
create policy "strategies_select_own" on public.strategies for select to authenticated using ((select auth.uid()) = user_id);
drop policy if exists "strategies_insert_own" on public.strategies;
create policy "strategies_insert_own" on public.strategies for insert to authenticated with check ((select auth.uid()) = user_id);
drop policy if exists "strategies_update_own" on public.strategies;
create policy "strategies_update_own" on public.strategies for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
drop policy if exists "strategies_delete_own" on public.strategies;
create policy "strategies_delete_own" on public.strategies for delete to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "executions_select_own" on public.executions;
create policy "executions_select_own" on public.executions for select to authenticated using (
  exists (select 1 from public.strategies s where s.id = strategy_id and s.user_id = (select auth.uid()))
);
drop policy if exists "execution_orders_select_own" on public.execution_orders;
create policy "execution_orders_select_own" on public.execution_orders for select to authenticated using (
  exists (
    select 1 from public.executions e
    join public.strategies s on s.id = e.strategy_id
    where e.id = execution_id and s.user_id = (select auth.uid())
  )
);

revoke all on function public.store_delta_connection(uuid,text,text,text,text,text) from public, anon, authenticated;
revoke all on function public.get_delta_credentials(uuid) from public, anon, authenticated;
revoke all on function public.delete_delta_connection(uuid) from public, anon, authenticated;
grant execute on function public.store_delta_connection(uuid,text,text,text,text,text) to service_role;
grant execute on function public.get_delta_credentials(uuid) to service_role;
grant execute on function public.delete_delta_connection(uuid) to service_role;

grant select, insert, update, delete on public.profiles to authenticated;
grant select, insert, update, delete on public.strategies to authenticated;
grant select on public.executions, public.execution_orders to authenticated;

commit;
