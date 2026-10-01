-- Rollback for supabase/migrations/20260930182226_free_sources_phases_0_2.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: coverage_status, exposure_mapping, instrument_security_link, issuer, macro_observation, provider_access, provider_identifier, provider_quota_usage, security, security_listing, source_conflict, source_document, source_document_version, source_evidence, source_observation, trade_observation.
-- PRECONDITION: no row may use a value the restored CHECK constraint (macro_series_source_check) forbids; the rollback stops with a clear error before changing anything if one does.

do $$ begin
  if exists (select 1 from public.macro_series where not ((source = ANY (ARRAY['fred'::text, 'ecb'::text, 'eurostat'::text])))) then
    raise exception 'rollback precondition failed: public.macro_series holds rows that violate the restored constraint macro_series_source_check; delete or migrate those rows first (select * from public.macro_series where not ((source = ANY (ARRAY[''fred''::text, ''ecb''::text, ''eurostat''::text]))))';
  end if;
end $$;
alter table public.macro_series drop constraint if exists macro_series_source_check;
alter table public.macro_series add constraint macro_series_source_check CHECK ((source = ANY (ARRAY['fred'::text, 'ecb'::text, 'eurostat'::text])));
drop table if exists public.coverage_status cascade;
drop table if exists public.exposure_mapping cascade;
drop table if exists public.instrument_security_link cascade;
drop table if exists public.issuer cascade;
drop table if exists public.macro_observation cascade;
drop table if exists public.provider_access cascade;
drop table if exists public.provider_identifier cascade;
drop table if exists public.provider_quota_usage cascade;
drop table if exists public.security cascade;
drop table if exists public.security_listing cascade;
drop table if exists public.source_conflict cascade;
drop table if exists public.source_document cascade;
drop table if exists public.source_document_version cascade;
drop table if exists public.source_evidence cascade;
drop table if exists public.source_observation cascade;
drop table if exists public.trade_observation cascade;
