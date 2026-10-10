-- Rollback for supabase/migrations/20261020100000_release_validation.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every source-audit record, pilot window and page-use row.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.usage_event;
drop table if exists public.pilot_run;
drop table if exists public.source_audit;
