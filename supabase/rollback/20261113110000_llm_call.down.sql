-- Rollback for supabase/migrations/20261113110000_llm_call.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: every captured model call is dropped; nothing in the app reads it yet.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.llm_call;
