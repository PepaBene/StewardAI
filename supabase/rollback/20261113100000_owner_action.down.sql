-- Rollback for supabase/migrations/20261113100000_owner_action.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the record of owner actions (screen and chat) is dropped.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.owner_action;
