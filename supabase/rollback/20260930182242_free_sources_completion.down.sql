-- Rollback for supabase/migrations/20260930182242_free_sources_completion.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: identity_match_decision, issuer_alias, issuer_relationship, macro_payload_archive, macro_series_metadata, provider_endpoint_manifest, provider_fetch_audit.
-- DATA LOSS: drops these columns and their values: provider_access.configuration_hash, provider_access.hard_blocked, provider_access.last_success_at, provider_quota_usage.window_kind.
-- Data note: provider_quota_usage minute/month usage windows are discarded so the original (provider, endpoint, window_start) key can be restored; daily windows are kept.

delete from public.provider_quota_usage where window_kind <> 'day';
alter table public.provider_quota_usage drop constraint if exists provider_quota_usage_pkey;
alter table public.provider_quota_usage drop constraint if exists provider_quota_usage_window_kind_check;
alter table public.provider_access drop column if exists configuration_hash cascade;
alter table public.provider_access drop column if exists hard_blocked cascade;
alter table public.provider_access drop column if exists last_success_at cascade;
alter table public.provider_quota_usage drop column if exists window_kind cascade;
alter table public.provider_quota_usage add constraint provider_quota_usage_pkey PRIMARY KEY (provider, endpoint, window_start);
drop table if exists public.identity_match_decision cascade;
drop table if exists public.issuer_alias cascade;
drop table if exists public.issuer_relationship cascade;
drop table if exists public.macro_payload_archive cascade;
drop table if exists public.macro_series_metadata cascade;
drop table if exists public.provider_endpoint_manifest cascade;
drop table if exists public.provider_fetch_audit cascade;
drop function if exists public.record_provider_attempt(p_provider text, p_endpoint text, p_state text, p_detail text, p_retry_at timestamp with time zone, p_status_code integer, p_request_id text, p_elapsed_ms integer, p_response_bytes bigint, p_credits numeric);
drop function if exists public.reserve_provider_credits(p_provider text, p_endpoint text, p_cost numeric, p_per_minute numeric, p_daily numeric, p_monthly numeric, p_reserve numeric, p_now timestamp with time zone);
delete from public.provider_access where provider='alpha_vantage' and endpoint='company_overview';
delete from public.provider_access where provider='alpha_vantage' and endpoint='news_sentiment';
delete from public.provider_access where provider='bis' and endpoint='sdmx';
delete from public.provider_access where provider='cnb' and endpoint='series';
delete from public.provider_access where provider='diffbot' and endpoint='extract';
delete from public.provider_access where provider='ecb' and endpoint='sdmx';
delete from public.provider_access where provider='eodhd' and endpoint='eod';
delete from public.provider_access where provider='epo_ops' and endpoint='published_data';
delete from public.provider_access where provider='eurostat' and endpoint='dissemination';
delete from public.provider_access where provider='finnhub' and endpoint='earnings_calendar';
delete from public.provider_access where provider='finnhub' and endpoint='ownership';
delete from public.provider_access where provider='fmp' and endpoint='analyst_estimates';
delete from public.provider_access where provider='fmp' and endpoint='profile';
delete from public.provider_access where provider='fred' and endpoint='observations';
delete from public.provider_access where provider='fred' and endpoint='vintages';
delete from public.provider_access where provider='oecd' and endpoint='sdmx';
delete from public.provider_access where provider='people_data_labs' and endpoint='company_enrich';
delete from public.provider_access where provider='twelve_data' and endpoint='corporate_actions';
delete from public.provider_access where provider='twelve_data' and endpoint='time_series';
delete from public.provider_access where provider='un_comtrade' and endpoint='trade';
delete from public.provider_access where provider='world_bank' and endpoint='indicators';
