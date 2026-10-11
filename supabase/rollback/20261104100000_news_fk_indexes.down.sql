-- Rollback for supabase/migrations/20261104100000_news_fk_indexes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- No row data is lost; only the two indexes are removed (and the earlier partial index restored).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop index if exists public.news_incident_review_story;
create index story_cluster_thread on public.story_cluster (thread_id) where thread_id is not null;
drop index if exists public.story_cluster_thread_id;
