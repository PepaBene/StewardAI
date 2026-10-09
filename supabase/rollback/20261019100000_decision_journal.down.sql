-- Rollback for supabase/migrations/20261019100000_decision_journal.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every decision journal entry, with its reasons and evidence snapshot.
-- Export the journal first (Journal > Scorecard > Download the journal).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.decision_journal;
