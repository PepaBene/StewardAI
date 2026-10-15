-- Rollback for supabase/migrations/20261111100000_engagement_event.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the record of what the owner opened is dropped; tuning that depends on it simply
-- has no signal until new opens are recorded.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.engagement_event;
