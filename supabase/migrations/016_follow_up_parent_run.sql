-- Preserve the scheduling decision without copying a report before it has finished.
begin;

alter table public.automation_agent_runs
  add column if not exists parent_agent_run_id uuid
    references public.automation_agent_runs(id) on delete set null
    check (parent_agent_run_id <> id);

create index if not exists automation_runs_parent_idx
  on public.automation_agent_runs(parent_agent_run_id)
  where parent_agent_run_id is not null;

-- Older pending follow-ups retain their parent's snapshot. Backfill only exact, unique matches.
with parents as (
  select child.id, (array_agg(parent.id))[1] as parent_id
  from public.automation_agent_runs child
  join public.automation_agent_runs parent
    on parent.market_snapshot_id = child.market_snapshot_id
    and parent.user_id = child.user_id and parent.id <> child.id
    and parent.outcome = 'wait_and_run_again'
    and parent.scheduled_for < child.scheduled_for
  where child.trigger = 'agent_follow_up' and child.status = 'scheduled'
    and child.parent_agent_run_id is null
  group by child.id
  having count(*) = 1
)
update public.automation_agent_runs child
set parent_agent_run_id = parents.parent_id
from parents where child.id = parents.id;

commit;
