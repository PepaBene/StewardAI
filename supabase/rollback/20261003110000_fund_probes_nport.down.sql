-- Rollback for supabase/migrations/20261003110000_fund_probes_nport.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: fund_probe_record, nport_filing, nport_fund_map, nport_holding.
-- PRECONDITION: no row may use a value the restored CHECK constraint (source_record_evidence_domain_table_check) forbids; the rollback stops with a clear error before changing anything if one does.

do $$ begin
  if exists (select 1 from public.source_record_evidence where not ((domain_table = ANY (ARRAY['news_item'::text, 'fact'::text, 'form13f_filing'::text, 'institutional_holding'::text, 'insider_filing'::text, 'insider_transaction'::text])))) then
    raise exception 'rollback precondition failed: public.source_record_evidence holds rows that violate the restored constraint source_record_evidence_domain_table_check; delete or migrate those rows first (select * from public.source_record_evidence where not ((domain_table = ANY (ARRAY[''news_item''::text, ''fact''::text, ''form13f_filing''::text, ''institutional_holding''::text, ''insider_filing''::text, ''insider_transaction''::text]))))';
  end if;
end $$;
alter table public.source_record_evidence drop constraint if exists source_record_evidence_domain_table_check;
alter table public.source_record_evidence add constraint source_record_evidence_domain_table_check CHECK ((domain_table = ANY (ARRAY['news_item'::text, 'fact'::text, 'form13f_filing'::text, 'institutional_holding'::text, 'insider_filing'::text, 'insider_transaction'::text])));
drop table if exists public.fund_probe_record cascade;
drop table if exists public.nport_filing cascade;
drop table if exists public.nport_fund_map cascade;
drop table if exists public.nport_holding cascade;
delete from public.provider_access where provider='finnhub' and endpoint='etf_profile';
delete from public.provider_access where provider='finnhub' and endpoint='institutional_ownership';
delete from public.provider_access where provider='fmp' and endpoint='etf_holdings';
delete from public.provider_access where provider='fmp' and endpoint='etf_info';
delete from public.provider_access where provider='fmp' and endpoint='institutional_ownership';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='etf_profile';
delete from public.provider_endpoint_manifest where provider='finnhub' and endpoint='institutional_ownership';
delete from public.provider_endpoint_manifest where provider='fmp' and endpoint='etf_holdings';
delete from public.provider_endpoint_manifest where provider='fmp' and endpoint='etf_info';
delete from public.provider_endpoint_manifest where provider='fmp' and endpoint='institutional_ownership';
