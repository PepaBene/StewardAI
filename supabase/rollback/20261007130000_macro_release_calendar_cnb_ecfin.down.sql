-- Rollback for supabase/migrations/20261007130000_macro_release_calendar_cnb_ecfin.sql (plan D8.4).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is
-- run, and the catalog fingerprint (tables, columns, constraints, indexes, functions, policies,
-- grants, seed rows) must equal the one taken before the migration. Run it in a transaction,
-- newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the stored ČNB release dates (provider 'cnb', required to restore the
-- narrower provider check), the two `release_calendar` provider_access / manifest rows of
-- providers 'ecfin' and 'cnb' with their fetch-audit and quota-usage rows, and their coverage
-- rows. The macro context then falls back to the observed-cadence estimate for cnb_repo_2w; the
-- sentiment dates stored under provider 'eurostat' are ordinary rows and stay.

-- the table is append-only (trigger): lift it for this one deliberate deletion
alter table public.macro_release_calendar disable trigger append_only;
delete from public.macro_release_calendar where provider = 'cnb';
alter table public.macro_release_calendar enable trigger append_only;
delete from public.coverage_status where provider = 'macro_release_calendar'
  and dataset in ('ecfin','cnb');
delete from public.provider_fetch_audit where endpoint = 'release_calendar'
  and provider in ('ecfin','cnb');
delete from public.provider_quota_usage where endpoint = 'release_calendar'
  and provider in ('ecfin','cnb');
delete from public.provider_access where endpoint = 'release_calendar'
  and provider in ('ecfin','cnb');  -- the manifest rows cascade
alter table public.macro_release_calendar
  drop constraint macro_release_calendar_provider_check;
alter table public.macro_release_calendar
  add constraint macro_release_calendar_provider_check
  check (provider in ('fred','ecb','eurostat','bis'));
