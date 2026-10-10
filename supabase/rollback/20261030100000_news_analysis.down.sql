-- Rollback for supabase/migrations/20261030100000_news_analysis.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: stored story analysis, threads, theme counts, briefs and the owner's marks.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop index if exists public.story_cluster_thread;
alter table public.story_cluster drop column if exists thread_id;
drop table if exists public.story_thread_update;
drop table if exists public.story_thread;
drop table if exists public.story_analysis;
drop table if exists public.news_theme_day;
drop table if exists public.news_brief;
drop table if exists public.news_feedback;
