-- Serialize automation runs per account and keep agent follow-ups bounded.
begin;

alter table public.automation_settings
  alter column maximum_agent_runs_per_day set default 3;

update public.automation_settings
set maximum_agent_runs_per_day = least(maximum_agent_runs_per_day, 3)
where maximum_agent_runs_per_day > 3;

create index if not exists automation_runs_user_schedule_idx
  on public.automation_agent_runs(user_id, scheduled_for)
  where status = 'scheduled';

create index if not exists automation_runs_running_started_idx
  on public.automation_agent_runs(started_at)
  where status = 'running';

create or replace function public.claim_automation_agent_run(
  p_user_id uuid,
  p_run_id uuid
)
returns table (
  id uuid,
  user_id uuid,
  trigger text,
  reason text,
  signals_to_inspect jsonb,
  scheduled_for timestamptz
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
            candidate.signals_to_inspect, candidate.scheduled_for;
end;
$$;

revoke all on function public.claim_automation_agent_run(uuid, uuid) from public, anon, authenticated;
grant execute on function public.claim_automation_agent_run(uuid, uuid) to service_role;

create or replace function public.ensure_automation_fixed_runs(p_runs jsonb)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  insert into public.automation_agent_runs (
    user_id, run_key, trigger, status, scheduled_for, model_id, reason
  )
  select item.user_id, item.run_key, item.trigger, 'scheduled',
         item.scheduled_for, item.model_id, item.reason
  from jsonb_to_recordset(p_runs) as item(
    user_id uuid,
    run_key text,
    trigger text,
    scheduled_for timestamptz,
    model_id text,
    reason text
  )
  on conflict (user_id, run_key) do update
  set status = 'scheduled', scheduled_for = excluded.scheduled_for,
      completed_at = null, error = null
  where public.automation_agent_runs.status = 'cancelled'
    and excluded.scheduled_for > now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.ensure_automation_fixed_runs(jsonb) from public, anon, authenticated;
grant execute on function public.ensure_automation_fixed_runs(jsonb) to service_role;

create or replace function public.cancel_redundant_automation_followups()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  update public.automation_agent_runs follow_up
  set status = 'cancelled', completed_at = now(),
      error = 'A fixed session review is already scheduled first'
  where follow_up.status = 'scheduled'
    and follow_up.trigger = 'agent_follow_up'
    and exists (
      select 1
      from public.automation_agent_runs fixed
      where fixed.user_id = follow_up.user_id
        and fixed.status = 'scheduled'
        and fixed.trigger in ('asia_session', 'london_session', 'new_york_session')
        and fixed.scheduled_for <= follow_up.scheduled_for
    );
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.cancel_redundant_automation_followups() from public, anon, authenticated;
grant execute on function public.cancel_redundant_automation_followups() to service_role;

commit;
