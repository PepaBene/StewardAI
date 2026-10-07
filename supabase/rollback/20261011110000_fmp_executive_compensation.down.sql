-- Rollback for supabase/migrations/20261011110000_fmp_executive_compensation.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: removes the executive_compensation endpoint rows and the stored compensation
-- observations read through it.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

delete from public.company_research_observation where dataset = 'executive_compensation';
delete from public.provider_endpoint_manifest
  where provider = 'fmp' and endpoint = 'executive_compensation';
delete from public.provider_access
  where provider = 'fmp' and endpoint = 'executive_compensation';
