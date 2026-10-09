-- Rollback for supabase/migrations/20261008110000_adjudication_suggestions.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every stored model suggestion for the adjudication samples; labels people
-- recorded are untouched.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.disclosure_adjudication_suggestion;
