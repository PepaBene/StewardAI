-- Rollback for supabase/migrations/20261014100000_performance_config.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every saved version of the `performance` config domain, so settings saved
-- after the move are gone (the older `performance_setting` rows are untouched). config_version
-- rows are immutable, so the guard trigger is switched off for the delete and back on.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

alter table public.config_version disable trigger config_version_immutable;
update public.config_version set parent_id = null
  where parent_id in (select id from public.config_version where domain = 'performance');
delete from public.config_version where domain = 'performance';
alter table public.config_version enable trigger config_version_immutable;

alter table public.config_version drop constraint if exists config_version_domain_known;
alter table public.config_version add constraint config_version_domain_known check (domain in (
  'strategy', 'theses', 'holding_profiles', 'news_sources', 'event_rules',
  'notifications', 'research_profiles', 'verification', 'jobs', 'budget', 'prompts'
));
