-- Performance settings become a versioned config domain (plan M4.1-M4.9), so they have config
-- history, diff, rollback, export and import like every other domain.
--
-- `public.performance_setting` stays as the record of what was saved before this change; the
-- newest row of each owner is copied in as the first active version. Nothing writes to the old
-- table any more.

alter table public.config_version drop constraint if exists config_version_domain_known;
alter table public.config_version add constraint config_version_domain_known check (domain in (
  'strategy', 'theses', 'holding_profiles', 'news_sources', 'event_rules',
  'notifications', 'research_profiles', 'verification', 'jobs', 'budget', 'prompts',
  'performance'
));

insert into public.config_version (domain, body, schema_version, note, created_by, active)
select 'performance', s.settings, 1, 'Moved from performance_setting', s.owner_id, true
from public.performance_setting s
where s.id = (select max(id) from public.performance_setting)
  and not exists (select 1 from public.config_version where domain = 'performance');
