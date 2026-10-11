-- Rollback for supabase/migrations/20261105100000_research_plan_draft.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- The unstarted research plan drafts are lost (a started draft's plan is also kept in its report).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.research_plan_draft;
