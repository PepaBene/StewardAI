-- Rollback for supabase/migrations/20261102100000_news_incident_review.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the owner's incident statuses and notes are deleted.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.news_incident_review;
