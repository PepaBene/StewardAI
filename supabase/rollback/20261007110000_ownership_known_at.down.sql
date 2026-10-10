-- Rollback for supabase/migrations/20261007110000_ownership_known_at.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- known_at is derived data (end of filing day UTC), so dropping it loses nothing the `filed`
-- column does not already hold. Any real acceptance time a later collector stored in it is lost.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.
drop index if exists public.nport_filing_known;
drop index if exists public.control_stake_filing_known;
drop index if exists public.form13f_filing_known;
drop trigger if exists nport_filing_known_at on public.nport_filing;
drop trigger if exists control_stake_filing_known_at on public.control_stake_filing;
drop trigger if exists form13f_filing_known_at on public.form13f_filing;
alter table public.nport_filing drop column if exists known_at;
alter table public.control_stake_filing drop column if exists known_at;
alter table public.form13f_filing drop column if exists confidential_treatment;
alter table public.form13f_filing drop column if exists known_at;
drop function if exists public.ownership_known_at_default();
