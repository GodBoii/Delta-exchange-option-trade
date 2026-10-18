-- Keep current configuration and queued work aligned with the deployed agents.
-- Completed and running jobs retain the model that actually handled them.
update trade.system_settings
set analysis = jsonb_set(analysis, '{model_id}', '"deepseek/deepseek-v4.1-flash-1017"'),
    revision = revision + 1
where analysis->>'model_id' is distinct from 'deepseek/deepseek-v4.1-flash-1017';

update trade.users
set automation = jsonb_set(automation, '{model_id}', '"deepseek/deepseek-v4.1-flash-1017"'),
    record = jsonb_set(record, '{automation}',
        jsonb_set(automation, '{model_id}', '"deepseek/deepseek-v4.1-flash-1017"')),
    revision = revision + 1
where automation->>'model_id' is distinct from 'deepseek/deepseek-v4.1-flash-1017'
   or record->'automation'->>'model_id' is distinct from 'deepseek/deepseek-v4.1-flash-1017';

update trade.analysis_jobs
set data = jsonb_set(data, '{model_id}', '"deepseek/deepseek-v4.1-flash-1017"'),
    revision = revision + 1
where status = 'scheduled'
  and data->>'model_id' is distinct from 'deepseek/deepseek-v4.1-flash-1017';
