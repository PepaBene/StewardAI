-- Rollback for supabase/migrations/20261007120000_macro_release_calendar.sql (plan D8.4).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is
-- run, and the catalog fingerprint (tables, columns, constraints, indexes, functions, policies,
-- grants, seed rows) must equal the one taken before the migration. Run it in a transaction,
-- newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops public.macro_release_calendar with every stored release date and its
-- reschedule history, deletes the four `release_calendar` provider_access / manifest rows with
-- their fetch-audit and quota-usage rows, and the coverage rows of provider
-- 'macro_release_calendar'. The macro context then falls back to the observed-cadence estimate.

delete from public.coverage_status where provider = 'macro_release_calendar';
delete from public.provider_fetch_audit where endpoint = 'release_calendar'
  and provider in ('fred','eurostat','ecb','bis');
delete from public.provider_quota_usage where endpoint = 'release_calendar'
  and provider in ('fred','eurostat','ecb','bis');
delete from public.provider_access where endpoint = 'release_calendar'
  and provider in ('fred','eurostat','ecb','bis');  -- the manifest rows cascade
drop table public.macro_release_calendar;
