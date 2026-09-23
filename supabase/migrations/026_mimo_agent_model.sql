-- Move current and future automation agent rows to Xiaomi MiMo V2.6 Pro.
begin;

alter table public.automation_settings
  alter column model_id set default 'xiaomi/mimo-v2.6-pro';

alter table public.automation_agent_runs
  alter column model_id set default 'xiaomi/mimo-v2.6-pro';

update public.automation_settings
set model_id = 'xiaomi/mimo-v2.6-pro',
    updated_at = now()
where model_id is distinct from 'xiaomi/mimo-v2.6-pro';

update public.automation_agent_runs
set model_id = 'xiaomi/mimo-v2.6-pro',
    updated_at = now()
where status = 'scheduled'
  and model_id is distinct from 'xiaomi/mimo-v2.6-pro';

commit;
