-- Store the eight built-in strategies once and share them with every account.
-- User-created strategies remain private rows owned by their account.
begin;

alter table public.saved_strategies
  add column if not exists is_default boolean not null default false;

alter table public.saved_strategies
  alter column user_id drop not null;

create temporary table shared_default_migration_state on commit drop as
select
  count(*) = 0 as should_consolidate,
  count(*)::integer as existing_default_count
from public.saved_strategies
where is_default = true;

comment on column public.saved_strategies.is_default is
  'True only for the eight shared, immutable strategy templates.';

-- Fail closed if the current per-user seed data is not the expected four copies
-- of each built-in strategy. Custom strategies with different names are left alone.
do $$
declare
  should_consolidate boolean;
  existing_default_count integer;
  seeded_count integer;
  malformed_count integer;
begin
  select state.should_consolidate, state.existing_default_count
  into should_consolidate, existing_default_count
  from shared_default_migration_state state;

  if existing_default_count not in (0, 8) then
    raise exception 'Expected either zero or eight shared defaults. No rows were changed.';
  end if;
  if not should_consolidate then
    return;
  end if;

  select count(*) into seeded_count
  from public.saved_strategies
  where user_id is not null
    and name in (
      'Long call', 'Long put', 'Long ATM straddle', 'Long strangle',
      'Short ATM straddle', 'Short strangle', 'Iron condor', 'Iron butterfly'
    );

  select count(*) into malformed_count
  from (
    select name
    from public.saved_strategies
    where user_id is not null
      and name in (
        'Long call', 'Long put', 'Long ATM straddle', 'Long strangle',
        'Short ATM straddle', 'Short strangle', 'Iron condor', 'Iron butterfly'
      )
    group by name
    having count(*) <> 4
  ) unexpected;

  if seeded_count not in (0, 32) or malformed_count <> 0 then
    raise exception 'Expected either no seeded defaults or four copies of each of the eight defaults. No rows were changed.';
  end if;
end;
$$;

-- Canonical definitions from backend/app/default_strategies.py. Fixed dates are
-- harmless because the builder refreshes an expired schedule when a template opens.
insert into public.saved_strategies (
  id, user_id, name, definition_json, version, enabled_for_ai, is_default
)
values
  ('10000000-0000-4000-8000-000000000001', null, 'Long call', $json${"schemaVersion":2,"version":1,"name":"Long call","description":"Buy an ATM call when BTC has a strong bullish forecast and the expected move exceeds the premium hurdle.","category":"premium_buying","marketOutlook":"bullish","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"intraday","expiryPolicy":"7_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"strategy_level","riskBasis":"net_debit","stopLossPercent":100.0,"takeProfitPercent":50.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":false,"legs":[{"id":"long-call","lots":1,"position":"buy","optionType":"call","expiry":"2026-09-03","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"long_call","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true),
  ('10000000-0000-4000-8000-000000000002', null, 'Long put', $json${"schemaVersion":2,"version":1,"name":"Long put","description":"Buy an ATM put when BTC has a strong bearish forecast and the expected fall exceeds the premium hurdle.","category":"premium_buying","marketOutlook":"bearish","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"intraday","expiryPolicy":"7_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"strategy_level","riskBasis":"net_debit","stopLossPercent":100.0,"takeProfitPercent":50.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":false,"legs":[{"id":"long-put","lots":1,"position":"buy","optionType":"put","expiry":"2026-09-03","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"long_put","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true),
  ('10000000-0000-4000-8000-000000000003', null, 'Long ATM straddle', $json${"schemaVersion":2,"version":1,"name":"Long ATM straddle","description":"Buy the same-expiry ATM call and put when a large move is likely but its direction is unclear.","category":"premium_buying","marketOutlook":"large_move_unknown_direction","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"intraday","expiryPolicy":"same_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"strategy_level","riskBasis":"net_debit","stopLossPercent":100.0,"takeProfitPercent":50.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":true,"legs":[{"id":"long-straddle-call","lots":1,"position":"buy","optionType":"call","expiry":"2026-08-28","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"long_call","reentryOnTarget":0,"reentryOnStop":0},{"id":"long-straddle-put","lots":1,"position":"buy","optionType":"put","expiry":"2026-08-28","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"long_put","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true),
  ('10000000-0000-4000-8000-000000000004', null, 'Long strangle', $json${"schemaVersion":2,"version":1,"name":"Long strangle","description":"Buy OTM calls and puts when a very large move is expected and a lower initial debit is preferred.","category":"premium_buying","marketOutlook":"very_large_move_unknown_direction","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"intraday","expiryPolicy":"7_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"strategy_level","riskBasis":"net_debit","stopLossPercent":100.0,"takeProfitPercent":50.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":true,"legs":[{"id":"long-strangle-call","lots":1,"position":"buy","optionType":"call","expiry":"2026-09-03","strikeMode":"otm","strikeSteps":2,"orderType":"market_order","role":"long_call","reentryOnTarget":0,"reentryOnStop":0},{"id":"long-strangle-put","lots":1,"position":"buy","optionType":"put","expiry":"2026-09-03","strikeMode":"otm","strikeSteps":2,"orderType":"market_order","role":"long_put","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true),
  ('10000000-0000-4000-8000-000000000005', null, 'Short ATM straddle', $json${"schemaVersion":2,"version":1,"name":"Short ATM straddle","description":"Sell the same-expiry ATM call and put on a quiet day with strong sideways evidence and low event risk.","category":"premium_selling","marketOutlook":"sideways","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"hold_to_expiry","expiryPolicy":"same_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"combined_premium","riskBasis":"net_credit","stopLossPercent":100.0,"takeProfitPercent":50.0,"combinedStopLossPercent":100.0,"emergencyStopLossPercent":300.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":true,"legs":[{"id":"short-straddle-call","lots":1,"position":"sell","optionType":"call","expiry":"2026-08-28","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"short_call","reentryOnTarget":0,"reentryOnStop":0},{"id":"short-straddle-put","lots":1,"position":"sell","optionType":"put","expiry":"2026-08-28","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"short_put","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true),
  ('10000000-0000-4000-8000-000000000006', null, 'Short strangle', $json${"schemaVersion":2,"version":1,"name":"Short strangle","description":"Sell OTM calls and puts when BTC is expected to remain inside a wider range and volatility is low or falling.","category":"premium_selling","marketOutlook":"wide_sideways","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"hold_to_expiry","expiryPolicy":"same_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"combined_premium","riskBasis":"net_credit","stopLossPercent":100.0,"takeProfitPercent":50.0,"combinedStopLossPercent":100.0,"emergencyStopLossPercent":300.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":true,"legs":[{"id":"short-strangle-call","lots":1,"position":"sell","optionType":"call","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":2,"orderType":"market_order","role":"short_call","reentryOnTarget":0,"reentryOnStop":0},{"id":"short-strangle-put","lots":1,"position":"sell","optionType":"put","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":2,"orderType":"market_order","role":"short_put","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true),
  ('10000000-0000-4000-8000-000000000007', null, 'Iron condor', $json${"schemaVersion":2,"version":1,"name":"Iron condor","description":"Sell an OTM call and put with wider protective wings for a capped-risk range trade.","category":"defined_risk_premium_selling","marketOutlook":"wide_sideways","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"hold_to_expiry","expiryPolicy":"same_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"combined_premium","riskBasis":"defined_max_loss","stopLossPercent":100.0,"takeProfitPercent":50.0,"combinedStopLossPercent":100.0,"emergencyStopLossPercent":300.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":true,"legs":[{"id":"condor-protective-put","lots":1,"position":"buy","optionType":"put","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":4,"orderType":"market_order","role":"protective_put","reentryOnTarget":0,"reentryOnStop":0},{"id":"condor-short-put","lots":1,"position":"sell","optionType":"put","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":2,"orderType":"market_order","role":"short_put","reentryOnTarget":0,"reentryOnStop":0},{"id":"condor-short-call","lots":1,"position":"sell","optionType":"call","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":2,"orderType":"market_order","role":"short_call","reentryOnTarget":0,"reentryOnStop":0},{"id":"condor-protective-call","lots":1,"position":"buy","optionType":"call","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":4,"orderType":"market_order","role":"protective_call","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true),
  ('10000000-0000-4000-8000-000000000008', null, 'Iron butterfly', $json${"schemaVersion":2,"version":1,"name":"Iron butterfly","description":"Sell the ATM call and put with equal OTM wings when BTC is expected to stay close to ATM.","category":"defined_risk_premium_selling","marketOutlook":"tight_sideways","enabledForAi":true,"instrument":{"index":"BTCUSD","underlying":"BTC","underlyingFrom":"cash"},"entry":{"strategyType":"intraday","entryAt":"2026-08-27T00:15:00Z","exitAt":"2026-08-27T07:15:00Z"},"holdingMode":"hold_to_expiry","expiryPolicy":"same_day","exitMinutesBeforeExpiry":5,"sameExpiryRequired":true,"squareOff":"complete","riskMode":"combined_premium","riskBasis":"defined_max_loss","stopLossPercent":100.0,"takeProfitPercent":50.0,"combinedStopLossPercent":100.0,"emergencyStopLossPercent":300.0,"emergencyExitEnabled":true,"trailToBreakEven":false,"breakEvenScope":"all_legs","allocationMode":"full_balance","lotsMode":"auto","equalLotsRequired":true,"legs":[{"id":"butterfly-short-call","lots":1,"position":"sell","optionType":"call","expiry":"2026-08-28","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"short_call","reentryOnTarget":0,"reentryOnStop":0},{"id":"butterfly-short-put","lots":1,"position":"sell","optionType":"put","expiry":"2026-08-28","strikeMode":"atm","strikeSteps":0,"orderType":"market_order","role":"short_put","reentryOnTarget":0,"reentryOnStop":0},{"id":"butterfly-protective-call","lots":1,"position":"buy","optionType":"call","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":3,"orderType":"market_order","role":"protective_call","reentryOnTarget":0,"reentryOnStop":0},{"id":"butterfly-protective-put","lots":1,"position":"buy","optionType":"put","expiry":"2026-08-28","strikeMode":"otm","strikeSteps":3,"orderType":"market_order","role":"protective_put","reentryOnTarget":0,"reentryOnStop":0}],"acknowledgement":true}$json$, 1, true, true)
on conflict (id) do nothing;

create unique index if not exists saved_strategies_default_name_idx
  on public.saved_strategies (lower(name))
  where is_default = true;

-- A run owned by any account may reference a shared default or one of that
-- account's private strategies.
create or replace function public.validate_strategy_saved_definition()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.saved_strategy_id is not null and not exists (
    select 1 from public.saved_strategies saved
    where saved.id = new.saved_strategy_id
      and (saved.is_default = true or saved.user_id = new.user_id)
  ) then
    raise exception 'saved strategy must be shared or belong to the run owner';
  end if;
  return new;
end;
$$;

create or replace function public.validate_saved_strategy_source_run()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.is_default and new.source_run_id is not null then
    raise exception 'a shared default cannot have a source run';
  end if;
  if new.source_run_id is not null and not exists (
    select 1 from public.strategies run
    where run.id = new.source_run_id and run.user_id = new.user_id
  ) then
    raise exception 'source run must belong to the saved strategy owner';
  end if;
  return new;
end;
$$;

-- Keep stale clients from restoring the retired one-third slot allocation.
create or replace function public.bump_saved_strategy_version()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.definition_json ->> 'allocationMode' = 'one_of_three_account_slots' then
    new.definition_json = jsonb_set(
      new.definition_json - 'capitalAmount' - 'maximumLots',
      '{allocationMode}',
      '"full_balance"'::jsonb,
      true
    );
  end if;
  if old.definition_json is distinct from new.definition_json
     or old.name is distinct from new.name then
    new.version = old.version + 1;
  end if;
  new.enabled_for_ai = coalesce((new.definition_json ->> 'enabledForAi')::boolean, new.enabled_for_ai);
  return new;
end;
$$;

-- Relink immutable history and automation records before deleting the old
-- account-owned seed copies.
update public.strategies run
set saved_strategy_id = shared.id
from public.saved_strategies old,
     public.saved_strategies shared
where run.saved_strategy_id = old.id
  and (select should_consolidate from shared_default_migration_state)
  and old.user_id is not null
  and shared.is_default = true
  and shared.name = old.name
  and old.name in (
    'Long call', 'Long put', 'Long ATM straddle', 'Long strangle',
    'Short ATM straddle', 'Short strangle', 'Iron condor', 'Iron butterfly'
  );

update public.strategy_proposals proposal
set saved_strategy_id = shared.id
from public.saved_strategies old,
     public.saved_strategies shared
where proposal.saved_strategy_id = old.id
  and (select should_consolidate from shared_default_migration_state)
  and old.user_id is not null
  and shared.is_default = true
  and shared.name = old.name
  and old.name in (
    'Long call', 'Long put', 'Long ATM straddle', 'Long strangle',
    'Short ATM straddle', 'Short strangle', 'Iron condor', 'Iron butterfly'
  );

delete from public.saved_strategies
where user_id is not null
  and (select should_consolidate from shared_default_migration_state)
  and name in (
    'Long call', 'Long put', 'Long ATM straddle', 'Long strangle',
    'Short ATM straddle', 'Short strangle', 'Iron condor', 'Iron butterfly'
  );

alter table public.saved_strategies
  drop constraint if exists saved_strategies_owner_kind_check;
alter table public.saved_strategies
  add constraint saved_strategies_owner_kind_check check (
    (is_default = true and user_id is null and source_run_id is null)
    or (is_default = false and user_id is not null)
  );

drop policy if exists "saved_strategies_select_own" on public.saved_strategies;
create policy "saved_strategies_select_own"
on public.saved_strategies for select to authenticated
using (is_default = true or (select auth.uid()) = user_id);

drop policy if exists "saved_strategies_insert_own" on public.saved_strategies;
create policy "saved_strategies_insert_own"
on public.saved_strategies for insert to authenticated
with check (is_default = false and (select auth.uid()) = user_id);

drop policy if exists "saved_strategies_update_own" on public.saved_strategies;
create policy "saved_strategies_update_own"
on public.saved_strategies for update to authenticated
using (is_default = false and (select auth.uid()) = user_id)
with check (is_default = false and (select auth.uid()) = user_id);

drop policy if exists "saved_strategies_delete_own" on public.saved_strategies;
create policy "saved_strategies_delete_own"
on public.saved_strategies for delete to authenticated
using (is_default = false and (select auth.uid()) = user_id);

commit;
