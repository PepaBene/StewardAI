-- 20261001131038 rebuilt source_type_check without 'reference', which the reference-data
-- job's synthetic source (delisting and merger events) uses. Restore it.
alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss','page','edgar','finnhub','google_news','alpha_vantage','reference',
                  'macro','edgar_fts','rns','eqs'));
