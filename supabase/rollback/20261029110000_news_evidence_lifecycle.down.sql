-- Rollback for supabase/migrations/20261029110000_news_evidence_lifecycle.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: item provenance, story lifecycle states and their event history, wire topics.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.story_lifecycle_event;
drop index if exists public.news_article_topics;
alter table public.news_article drop column if exists topics;
alter table public.story_cluster
  drop column if exists lifecycle_closed_at,
  drop column if exists lifecycle_at,
  drop column if exists lifecycle_reason,
  drop column if exists lifecycle;
alter table public.news_item
  drop column if exists is_correction,
  drop column if exists independence_key,
  drop column if exists evidence_kind;
