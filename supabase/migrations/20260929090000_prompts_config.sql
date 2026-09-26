-- Prompts config: owner overrides of the AI steps' system prompts, a versioned config
-- domain like the others (the backend's PromptsConfig validates every body).

alter table public.config_version drop constraint if exists config_version_domain_known;
alter table public.config_version add constraint config_version_domain_known check (domain in (
  'strategy', 'theses', 'holding_profiles', 'news_sources', 'event_rules',
  'notifications', 'research_profiles', 'verification', 'jobs', 'budget', 'prompts'
));
