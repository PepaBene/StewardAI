-- Rollback for supabase/migrations/20261006160000_ai_result_cache.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the cached AI answers, so repeated requests are paid for again.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.ai_result_cache;
