-- Rollback for supabase/migrations/20261005180000_registry_filing_sources.sql (plan D8.4).
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the Companies House / EDINET access ledger rows, their attempt and quota
-- history, and the fmp / companies_house rows of company_research_observation (the restored
-- constraint does not allow those providers).
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

do $$
declare r record;
begin
  for r in select conrelid::regclass as t from pg_constraint
            where confrelid = 'public.provider_access'::regclass and contype = 'f' loop
    execute format('delete from %s where provider = any($1)', r.t)
      using array['companies_house','edinet'];
  end loop;
end $$;
delete from public.provider_endpoint_manifest where provider in ('companies_house','edinet');
delete from public.provider_access where provider in ('companies_house','edinet');

delete from public.company_research_observation where provider in ('fmp','companies_house');
alter table public.company_research_observation
  drop constraint if exists company_research_observation_provider_check;
alter table public.company_research_observation
  add constraint company_research_observation_provider_check
  CHECK ((provider = ANY (ARRAY['alpha_vantage'::text, 'pdl'::text, 'diffbot'::text, 'epo_ops'::text])));
