delete from public.provider_endpoint_manifest where (provider, endpoint) in
  (('yahoo','chart_dividends'), ('fmp','price_target_consensus'), ('finnhub','price_target'), ('twelve_data','price_target'));
delete from public.provider_access where (provider, endpoint) in
  (('yahoo','chart_dividends'), ('fmp','price_target_consensus'), ('finnhub','price_target'), ('twelve_data','price_target'));
delete from public.config_version where domain = 'checks';
alter table public.config_version drop constraint if exists config_version_domain_known;
alter table public.config_version add constraint config_version_domain_known check (domain in (
  'strategy', 'theses', 'holding_profiles', 'news_sources', 'event_rules',
  'notifications', 'research_profiles', 'verification', 'jobs', 'budget', 'prompts',
  'performance'
));
alter table public.company_valuation_scenario drop column if exists method;
drop table if exists public.company_check_snapshot;
drop table if exists public.dividend_forecast_snapshot;
drop table if exists public.consensus_snapshot;
drop table if exists public.fair_value_point;
drop function if exists public.insight_history_immutable();
