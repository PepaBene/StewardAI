-- Full-text search over news items, for the chat assistant's search_news tool.
--
-- - news_item.search: headline (weight A) and summary (weight B) under the 'english'
--   configuration, so word forms match ("cuts" finds "cut", "rates" finds "rate") and
--   results can be ranked. Tickers and names are kept as words ("sap", "novo", "nordisk").
--   A query made only of English stop words ("IT", "WHO") yields an empty tsquery; the
--   tool falls back to a plain substring match for those.
--
-- The column is generated, so the existing table-level grants and RLS policies cover it and
-- no writer changes. Adding it rewrites the table once (news items are small and few).

alter table public.news_item add column search tsvector generated always as (
  setweight(to_tsvector('english', coalesce(headline, '')), 'A')
    || setweight(to_tsvector('english', coalesce(summary, '')), 'B')
) stored;

create index news_item_search on public.news_item using gin (search);
