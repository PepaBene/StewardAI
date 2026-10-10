-- Rollback for supabase/migrations/20261025100000_switch_log.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every switch the owner logged; the log is append-only and cannot be rebuilt.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.switch_log;
