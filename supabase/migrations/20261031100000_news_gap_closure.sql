-- News coverage plan, gap closure after N0-N8.
-- - story_analysis.kind: + 'sentiment' (bounded aspect-level sentiment, a ranking input).
-- - news_brief.kind: + 'region' (the weekly regional digest) and 'theme' (the weekly narrative
--   of a watch theme, keyed by the theme id in `ticker`).
-- - news_theme_day.tone / attention: GDELT context for a theme-day.
-- - news_feedback.kind: + 'confirmed' (the owner confirms a story).
-- - news_article.triage / triaged_at: the luna triage of each wire item (relevance, topic,
--   entities and a one-line gloss), which decides what gets full text and analysis.

alter table public.story_analysis drop constraint if exists story_analysis_kind_check;
alter table public.story_analysis add constraint story_analysis_kind_check
  check (kind in ('extraction', 'novelty', 'impact', 'thread', 'framing', 'claims',
                  'market', 'thesis', 'sentiment'));

alter table public.news_brief drop constraint if exists news_brief_kind_check;
alter table public.news_brief add constraint news_brief_kind_check
  check (kind in ('morning', 'weekly', 'holding', 'region', 'theme'));

-- GDELT tone (negative below zero) and attention (share of monitored coverage) of a theme.
alter table public.news_theme_day
  add column tone real,
  add column attention real;

-- The owner can mark a story as confirmed; only that or a primary source lets a story breach a
-- thesis assumption.
alter table public.news_feedback drop constraint if exists news_feedback_kind_check;
alter table public.news_feedback add constraint news_feedback_kind_check
  check (kind in ('wrong_entity', 'duplicate', 'misread', 'missing_source', 'useful',
                  'not_useful', 'confirmed'));

alter table public.news_article
  add column triage jsonb,
  add column triaged_at timestamptz;

comment on column public.news_article.triage is
  '{"relevance": 0-3, "topic", "entities": [...], "gloss"}: the cheap-model triage of the item. Null until triaged.';

create index news_article_untriaged on public.news_article (published_at desc)
  where triaged_at is null;
