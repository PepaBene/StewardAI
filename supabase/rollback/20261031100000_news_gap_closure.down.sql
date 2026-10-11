-- Rollback for supabase/migrations/20261031100000_news_gap_closure.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: stored sentiment analysis, regional and theme digests, "confirmed" marks, GDELT
-- tone and attention, and the triage of every wire item are deleted, because the restored
-- constraints and columns no longer allow them.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop index if exists public.news_article_untriaged;
alter table public.news_article
  drop column if exists triaged_at,
  drop column if exists triage;

delete from public.news_feedback where kind = 'confirmed';
alter table public.news_feedback drop constraint if exists news_feedback_kind_check;
alter table public.news_feedback add constraint news_feedback_kind_check
  check (kind in ('wrong_entity', 'duplicate', 'misread', 'missing_source', 'useful',
                  'not_useful'));

alter table public.news_theme_day
  drop column if exists attention,
  drop column if exists tone;

delete from public.news_brief where kind in ('region', 'theme');
alter table public.news_brief drop constraint if exists news_brief_kind_check;
alter table public.news_brief add constraint news_brief_kind_check
  check (kind in ('morning', 'weekly', 'holding'));

delete from public.story_analysis where kind = 'sentiment';
alter table public.story_analysis drop constraint if exists story_analysis_kind_check;
alter table public.story_analysis add constraint story_analysis_kind_check
  check (kind in ('extraction', 'novelty', 'impact', 'thread', 'framing', 'claims',
                  'market', 'thesis'));
