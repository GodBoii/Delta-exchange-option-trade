-- Run manually in the Supabase SQL editor after the local import has been verified.
-- Analysis reports and Agno sessions now live in the Ubuntu PostgreSQL `ai` schema.
-- Supabase keeps Auth and public.profiles only.
--
-- Chart images in the `automation-charts` bucket were deleted through the Storage API
-- (Supabase blocks direct deletes from storage tables). The last statement removes the
-- empty bucket record if it still exists.

begin;

drop table if exists ai.automation_agent_sessions;
drop table if exists ai.news_agent_sessions;
drop table if exists ai.agno_schema_versions;
drop schema if exists ai;

drop table if exists public.analysis_reports;

delete from storage.buckets
where id = 'automation-charts'
  and not exists (select 1 from storage.objects where bucket_id = 'automation-charts');

commit;
