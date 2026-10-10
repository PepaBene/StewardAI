-- Rollback for supabase/migrations/20261028110000_news_language_dedupe.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: stored embeddings, English translations and duplicate-group links.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
revoke delete on public.story_cluster from app_backend;
drop table if exists public.news_embedding;
drop index if exists public.news_article_untranslated;
drop index if exists public.news_article_group;
alter table public.news_article
  drop column if exists group_method,
  drop column if exists group_id,
  drop column if exists translation_state,
  drop column if exists summary_en;
