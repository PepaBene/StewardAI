-- Rollback for supabase/migrations/20261028100000_news_wire_and_fulltext.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the wire (news_article), its full-text store, the per-source run log and the
-- new source columns. Sources of the types news_sitemap, gdelt and ai_search must be deleted
-- first, or the restored constraint fails loudly.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop view if exists public.news_source_health;
drop table if exists public.news_fulltext;
drop table if exists public.news_source_run;
drop table if exists public.news_article;

alter table public.source
  drop column if exists region,
  drop column if exists language,
  drop column if exists paywall,
  drop column if exists state_media,
  drop column if exists etag,
  drop column if exists http_last_modified,
  drop column if exists consecutive_failures,
  drop column if exists backoff_until,
  drop column if exists last_success_at,
  drop column if exists last_item_at;

alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss','page','edgar','finnhub','google_news','alpha_vantage','reference',
                  'macro','edgar_fts','rns','eqs'));
