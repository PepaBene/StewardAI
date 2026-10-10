-- Rollback for supabase/migrations/20261016100000_saved_analysis.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every saved what-if scenario and saved performance report.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.saved_analysis;
