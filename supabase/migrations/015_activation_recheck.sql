-- Bind a lightweight activation recheck to each AI-selected strategy.
begin;

alter table public.automation_agent_runs
  add column if not exists strategy_proposal_id uuid
    references public.strategy_proposals(id) on delete cascade;

alter table public.automation_agent_runs
  drop constraint if exists automation_agent_runs_outcome_check;
alter table public.automation_agent_runs
  add constraint automation_agent_runs_outcome_check check (
    outcome in (
      'strategy_selected', 'wait_and_run_again', 'no_trade_for_current_window',
      'strategy_reconfirmed', 'strategy_dropped'
    )
  );

create unique index if not exists automation_runs_one_activation_recheck_idx
  on public.automation_agent_runs(strategy_proposal_id)
  where trigger = 'activation_recheck';

update public.saved_strategies
set enabled_for_ai = false,
    definition_json = jsonb_set(definition_json, '{enabledForAi}', 'false'::jsonb, true)
where user_id is null and name in ('Iron condor', 'Iron butterfly');

drop function if exists public.claim_automation_agent_run(uuid, uuid);
create function public.claim_automation_agent_run(
  p_user_id uuid,
  p_run_id uuid
)
returns table (
  id uuid,
  user_id uuid,
  trigger text,
  reason text,
  signals_to_inspect jsonb,
  scheduled_for timestamptz,
  strategy_proposal_id uuid
)
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 41));

  if exists (
    select 1
    from public.automation_agent_runs running
    where running.user_id = p_user_id and running.status = 'running'
  ) then
    return;
  end if;

  return query
  update public.automation_agent_runs candidate
  set status = 'running', started_at = now(), error = null
  where candidate.id = p_run_id
    and candidate.user_id = p_user_id
    and candidate.status = 'scheduled'
  returning candidate.id, candidate.user_id, candidate.trigger, candidate.reason,
            candidate.signals_to_inspect, candidate.scheduled_for, candidate.strategy_proposal_id;
end;
$$;

revoke all on function public.claim_automation_agent_run(uuid, uuid) from public, anon, authenticated;
grant execute on function public.claim_automation_agent_run(uuid, uuid) to service_role;

commit;
