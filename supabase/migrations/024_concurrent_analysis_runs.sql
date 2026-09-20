-- Claim a particular run once; unrelated analyses and rechecks may run concurrently.
begin;

create or replace function public.claim_automation_agent_run(p_user_id uuid, p_run_id uuid)
returns table (
  id uuid, user_id uuid, trigger text, reason text, signals_to_inspect jsonb,
  scheduled_for timestamptz, strategy_proposal_id uuid
)
language sql security definer set search_path = ''
as $$
  update public.automation_agent_runs candidate
  set status = 'running', started_at = now(), error = null
  where candidate.id = p_run_id and candidate.user_id = p_user_id and candidate.status = 'scheduled'
  returning candidate.id, candidate.user_id, candidate.trigger, candidate.reason,
            candidate.signals_to_inspect, candidate.scheduled_for, candidate.strategy_proposal_id;
$$;

revoke all on function public.claim_automation_agent_run(uuid, uuid) from public, anon, authenticated;
grant execute on function public.claim_automation_agent_run(uuid, uuid) to service_role;

-- Move only pending rechecks that can still start seven minutes before entry.
update public.automation_agent_runs runs
set scheduled_for = proposals.activation_time - interval '7 minutes'
from public.strategy_proposals proposals
where runs.strategy_proposal_id = proposals.id and runs.trigger = 'activation_recheck'
  and runs.status = 'scheduled' and proposals.activation_time > now() + interval '7 minutes';

commit;
