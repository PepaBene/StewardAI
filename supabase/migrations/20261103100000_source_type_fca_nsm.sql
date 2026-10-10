-- News plan, FCA National Storage Mechanism: a new source type, fca_nsm (UK regulated
-- announcements from the FCA's own search API, no key).
alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss','page','edgar','finnhub','google_news','alpha_vantage','reference',
                  'macro','edgar_fts','rns','eqs','news_sitemap','gdelt','ai_search',
                  'tdnet','hkex','cninfo','dart','gazette','fca_nsm','filings'));
