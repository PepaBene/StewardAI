-- Rollback for supabase/migrations/20261001103217_free_sources_phases_3_5.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: company_event, company_graph_edge, corporate_action, exchange_session, financial_statement_fact, forward_observation, issuer_ir_release, patent_family, patent_family_owner, peer_set, price_return_series, relationship_disclosure.
-- DATA LOSS: drops these columns and their values: price_bar.access_state, price_bar.bar_type, price_bar.listing_id, price_bar.mic, price_bar.quote_unit, price_bar.session_date, price_bar.source_timestamp.

alter table public.price_bar drop constraint if exists price_bar_access_state_check;
alter table public.price_bar drop constraint if exists price_bar_bar_type_check;
alter table public.price_bar drop constraint if exists price_bar_listing_id_fkey;
drop index if exists public.price_bar_listing_session;
alter table public.price_bar drop column if exists access_state cascade;
alter table public.price_bar drop column if exists bar_type cascade;
alter table public.price_bar drop column if exists listing_id cascade;
alter table public.price_bar drop column if exists mic cascade;
alter table public.price_bar drop column if exists quote_unit cascade;
alter table public.price_bar drop column if exists session_date cascade;
alter table public.price_bar drop column if exists source_timestamp cascade;
drop table if exists public.company_event cascade;
drop table if exists public.company_graph_edge cascade;
drop table if exists public.corporate_action cascade;
drop table if exists public.exchange_session cascade;
drop table if exists public.financial_statement_fact cascade;
drop table if exists public.forward_observation cascade;
drop table if exists public.issuer_ir_release cascade;
drop table if exists public.patent_family cascade;
drop table if exists public.patent_family_owner cascade;
drop table if exists public.peer_set cascade;
drop table if exists public.price_return_series cascade;
drop table if exists public.relationship_disclosure cascade;
delete from public.provider_access where provider='alpha_vantage' and endpoint='commodity_fx';
delete from public.provider_access where provider='alpha_vantage' and endpoint='daily_adjusted';
delete from public.provider_access where provider='alpha_vantage' and endpoint='financial_statements';
delete from public.provider_access where provider='diffbot' and endpoint='organization';
delete from public.provider_access where provider='eodhd' and endpoint='symbol_search';
delete from public.provider_access where provider='epo_ops' and endpoint='patent_search';
delete from public.provider_access where provider='finnhub' and endpoint='earnings_surprises';
delete from public.provider_access where provider='finnhub' and endpoint='peers';
delete from public.provider_access where provider='finnhub' and endpoint='quote';
delete from public.provider_access where provider='twelve_data' and endpoint='quote';
delete from public.provider_endpoint_manifest where provider='alpha_vantage' and endpoint='commodity_fx';
delete from public.provider_endpoint_manifest where provider='alpha_vantage' and endpoint='daily_adjusted';
delete from public.provider_endpoint_manifest where provider='alpha_vantage' and endpoint='financial_statements';
delete from public.provider_endpoint_manifest where provider='diffbot' and endpoint='organization';
delete from public.provider_endpoint_manifest where provider='eodhd' and endpoint='symbol_search';
delete from public.provider_endpoint_manifest where provider='epo_ops' and endpoint='patent_search';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='earnings_surprises';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='peers';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='quote';
delete from public.provider_endpoint_manifest where provider='twelve_data' and endpoint='quote';
