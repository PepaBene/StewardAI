-- Rollback for supabase/migrations/20261001131038_free_source_research_consumers.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: company_research_observation.
-- DATA LOSS: drops these columns and their values: news_item.sentiment_relevance, news_item.sentiment_score.
-- PRECONDITION: no row may use a value the restored CHECK constraint (source_type_check) forbids; the rollback stops with a clear error before changing anything if one does.

do $$ begin
  if exists (select 1 from public.source where not ((type = ANY (ARRAY['rss'::text, 'page'::text, 'edgar'::text, 'finnhub'::text, 'google_news'::text, 'reference'::text, 'rns'::text, 'eqs'::text, 'edgar_fts'::text, 'macro'::text])))) then
    raise exception 'rollback precondition failed: public.source holds rows that violate the restored constraint source_type_check; delete or migrate those rows first (select * from public.source where not ((type = ANY (ARRAY[''rss''::text, ''page''::text, ''edgar''::text, ''finnhub''::text, ''google_news''::text, ''reference''::text, ''rns''::text, ''eqs''::text, ''edgar_fts''::text, ''macro''::text]))))';
  end if;
end $$;
alter table public.news_item drop constraint if exists news_item_sentiment_relevance_check;
alter table public.news_item drop constraint if exists news_item_sentiment_score_check;
alter table public.source drop constraint if exists source_type_check;
alter table public.news_item drop column if exists sentiment_relevance cascade;
alter table public.news_item drop column if exists sentiment_score cascade;
alter table public.source add constraint source_type_check CHECK ((type = ANY (ARRAY['rss'::text, 'page'::text, 'edgar'::text, 'finnhub'::text, 'google_news'::text, 'reference'::text, 'rns'::text, 'eqs'::text, 'edgar_fts'::text, 'macro'::text])));
drop table if exists public.company_research_observation cascade;
