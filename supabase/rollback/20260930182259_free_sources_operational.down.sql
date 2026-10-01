-- Rollback for supabase/migrations/20260930182259_free_sources_operational.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: legal_entity_snapshot, listing_lifecycle_snapshot, provider_benchmark_result, source_record_evidence.
-- DATA LOSS: drops these columns and their values: trade_observation.trade_currency.

drop view if exists public.provider_quota_status cascade;
drop trigger if exists exposure_total_guard on public.exposure_mapping;
alter table public.source_observation drop constraint if exists source_observation_access_state_check;
alter table public.trade_observation drop constraint if exists trade_observation_trade_currency_check;
alter table public.trade_observation drop column if exists trade_currency cascade;
drop table if exists public.legal_entity_snapshot cascade;
drop table if exists public.listing_lifecycle_snapshot cascade;
drop table if exists public.provider_benchmark_result cascade;
drop table if exists public.source_record_evidence cascade;
drop function if exists public.check_exposure_total();
delete from public.provider_access where provider='finnhub' and endpoint='etf_holdings';
delete from public.provider_access where provider='finnhub' and endpoint='forward_estimates';
delete from public.provider_access where provider='finnhub' and endpoint='index_constituents';
delete from public.provider_access where provider='fmp' and endpoint='sector_snapshot';
delete from public.provider_access where provider='twelve_data' and endpoint='splits';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='etf_holdings';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='forward_estimates';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='index_constituents';
delete from public.provider_endpoint_manifest where provider='fmp' and endpoint='sector_snapshot';
delete from public.provider_endpoint_manifest where provider='twelve_data' and endpoint='splits';
