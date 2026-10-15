-- Rollback for supabase/migrations/20261117100000_owner_override.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the record of owner overrides and their reasons is dropped.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.owner_override;
