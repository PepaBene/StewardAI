-- Rollback for supabase/migrations/20261024100010_factor_return.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the stored factor returns; the factor job downloads them again.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.factor_return;
