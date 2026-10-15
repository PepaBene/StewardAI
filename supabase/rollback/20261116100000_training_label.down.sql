-- Rollback for supabase/migrations/20261116100000_training_label.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: every recorded label is dropped; nothing in the app reads them yet.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.training_label;
