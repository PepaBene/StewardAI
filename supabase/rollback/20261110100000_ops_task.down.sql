-- Rollback for supabase/migrations/20261110100000_ops_task.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the watchdog's task history is dropped (what it fixed, how often it tried, and the
-- open notices in "Needs you"). The watchdog rebuilds the open ones on its next run.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.ops_task;
