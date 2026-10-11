-- Rollback for supabase/migrations/20261107100000_inbox_and_agent_note.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: inbox items (with the owner's feedback) and the owner-written agent notes are
-- dropped. Items can be regenerated from their triggers; feedback and notes cannot, so export
-- them first if they matter.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.agent_note;
drop table if exists public.inbox_item;
