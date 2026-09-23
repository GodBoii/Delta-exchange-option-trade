-- Apply only after the encrypted backup, field comparison and live API checks.
-- Auth, profiles, analysis reports, chart storage and native Agno tables remain.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

do $$
begin
  if exists (select 1 from public.strategies where status not in ('completed','cancelled')) then
    raise exception 'Resolve source trading exposure before retiring its tables';
  end if;
  if exists (select 1 from public.automation_agent_runs where status = 'running') then
    raise exception 'A source analysis is still running';
  end if;
  if exists (
    select 1 from public.automation_agent_runs a
    left join public.analysis_reports r on r.id = a.id
    where a.user_id in ('7eebbd1c-ca25-4701-9687-43b918cccea0','7915c0dc-7bd8-4033-b476-bd759ddfdfc5')
      and (r.id is null or r.report_markdown is distinct from a.report_markdown)
  ) then
    raise exception 'Source reports have not been restored';
  end if;
end;
$$;

-- These secrets have been verified after decrypting their Convex replacements.
delete from vault.secrets where id in (select vault_secret_id from public.exchange_connections);
drop table public.execution_orders, public.executions, public.strategy_capital_slots,
  public.strategy_proposals, public.automation_agent_runs, public.automation_market_snapshots,
  public.automation_settings, public.capital_settings, public.strategies,
  public.saved_strategies, public.exchange_connections cascade;

drop function if exists public.bump_saved_strategy_version();
drop function if exists public.cancel_redundant_automation_followups();
drop function if exists public.claim_automation_agent_run(uuid,uuid);
drop function if exists public.delete_delta_connection(uuid);
drop function if exists public.enforce_automation_follow_up_window();
drop function if exists public.ensure_automation_fixed_runs(jsonb);
drop function if exists public.get_delta_credentials(uuid);
drop function if exists public.handle_new_user();
drop function if exists public.release_strategy_capital_slot(uuid,uuid);
drop function if exists public.release_terminal_strategy_capital_slot();
drop function if exists public.reserve_strategy_capital_slot(uuid,uuid,smallint);
drop function if exists public.store_delta_connection(uuid,text,text,text,text,text);
drop function if exists public.strip_strategy_capital_fields();
drop function if exists public.validate_saved_strategy_source_run();
drop function if exists public.validate_strategy_saved_definition();
commit;
