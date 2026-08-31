-- Keep one optional agent follow-up inside each fixed-session interval.
begin;

create or replace function public.cancel_redundant_automation_followups()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  with ranked_follow_ups as (
    select follow_up.id,
           row_number() over (
             partition by follow_up.user_id, next_fixed.id
             order by follow_up.scheduled_for, follow_up.created_at, follow_up.id
           ) as interval_position
    from public.automation_agent_runs follow_up
    join lateral (
      select fixed.id
      from public.automation_agent_runs fixed
      where fixed.user_id = follow_up.user_id
        and fixed.status = 'scheduled'
        and fixed.trigger in ('asia_session', 'london_session', 'new_york_session')
        and fixed.scheduled_for > now()
        and fixed.scheduled_for >= follow_up.scheduled_for
      order by fixed.scheduled_for
      limit 1
    ) next_fixed on true
    where follow_up.status = 'scheduled'
      and follow_up.trigger = 'agent_follow_up'
  )
  update public.automation_agent_runs follow_up
  set status = 'cancelled', completed_at = now(),
      error = case
        when ranked.interval_position > 1 then 'Only one follow-up is allowed before the next fixed session review'
        else 'A fixed session review is already scheduled first'
      end
  from ranked_follow_ups ranked
  where follow_up.id = ranked.id
    and (
      ranked.interval_position > 1
      or exists (
        select 1
        from public.automation_agent_runs fixed
        where fixed.user_id = follow_up.user_id
          and fixed.status = 'scheduled'
          and fixed.trigger in ('asia_session', 'london_session', 'new_york_session')
          and fixed.scheduled_for <= follow_up.scheduled_for
      )
    );
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.cancel_redundant_automation_followups() from public, anon, authenticated;
grant execute on function public.cancel_redundant_automation_followups() to service_role;

create or replace function public.enforce_automation_follow_up_window()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_previous_fixed timestamptz;
  v_next_fixed timestamptz;
begin
  if new.trigger <> 'agent_follow_up' or new.status <> 'scheduled' then
    return new;
  end if;

  select min(fixed.scheduled_for) into v_next_fixed
  from public.automation_agent_runs fixed
  where fixed.user_id = new.user_id
    and fixed.status = 'scheduled'
    and fixed.trigger in ('asia_session', 'london_session', 'new_york_session')
    and fixed.scheduled_for > now();

  if v_next_fixed is null then
    raise exception using
      errcode = '23514',
      message = 'A follow-up requires an upcoming fixed session review';
  end if;
  if new.scheduled_for >= v_next_fixed then
    raise exception using
      errcode = '23514',
      message = 'A follow-up must run before the next fixed session review';
  end if;

  select max(fixed.scheduled_for) into v_previous_fixed
  from public.automation_agent_runs fixed
  where fixed.user_id = new.user_id
    and fixed.trigger in ('asia_session', 'london_session', 'new_york_session')
    and fixed.scheduled_for <= now();

  if exists (
    select 1
    from public.automation_agent_runs follow_up
    where follow_up.user_id = new.user_id
      and follow_up.id <> new.id
      and follow_up.trigger = 'agent_follow_up'
      and follow_up.status <> 'cancelled'
      and follow_up.scheduled_for > coalesce(v_previous_fixed, '-infinity'::timestamptz)
      and follow_up.scheduled_for < v_next_fixed
  ) then
    raise exception using
      errcode = '23514',
      message = 'Only one follow-up is allowed between fixed session reviews';
  end if;

  return new;
end;
$$;

drop trigger if exists enforce_automation_follow_up_window on public.automation_agent_runs;
create trigger enforce_automation_follow_up_window
before insert or update of user_id, trigger, status, scheduled_for
on public.automation_agent_runs
for each row execute function public.enforce_automation_follow_up_window();

select public.cancel_redundant_automation_followups();

commit;
