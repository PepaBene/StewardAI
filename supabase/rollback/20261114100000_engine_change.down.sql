-- Rollback for supabase/migrations/20261114100000_engine_change.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the record of changes the engine proposed to itself is dropped.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.engine_change;
