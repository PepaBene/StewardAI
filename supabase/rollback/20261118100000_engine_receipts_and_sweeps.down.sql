-- Rollback for supabase/migrations/20261118100000_engine_receipts_and_sweeps.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the sweep history and the stored provider message ids are dropped.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.engine_sweep;
alter table public.alert
  drop column if exists provider_id,
  drop column if exists provider_status,
  drop column if exists provider_checked_at;
