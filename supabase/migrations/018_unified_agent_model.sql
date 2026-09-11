-- Use one OpenRouter model for automation settings and all future agent runs.
begin;

alter table public.automation_settings
  alter column model_id set default 'deepseek/deepseek-v4.1-flash';

alter table public.automation_agent_runs
  alter column model_id set default 'deepseek/deepseek-v4.1-flash';

update public.automation_settings
set model_id = 'deepseek/deepseek-v4.1-flash',
    updated_at = now()
where model_id is distinct from 'deepseek/deepseek-v4.1-flash';

update public.automation_agent_runs
set model_id = 'deepseek/deepseek-v4.1-flash',
    updated_at = now()
where status = 'scheduled'
  and model_id is distinct from 'deepseek/deepseek-v4.1-flash';

commit;
