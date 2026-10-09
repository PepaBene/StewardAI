-- Rollback for supabase/migrations/20261021100000_quality_followup.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every snooze and every pilot defect record.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.pilot_defect;
drop table if exists public.quality_snooze;
