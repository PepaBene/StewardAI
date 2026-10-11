-- News coverage plan, phase N4: official disclosure sources by market.
-- New source types: tdnet (Tokyo), hkex (Hong Kong), cninfo (Shanghai/Shenzhen), dart (Korea),
-- gazette (UK), filings (EDINET and Companies House rows the disclosure jobs already stored).
alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss','page','edgar','finnhub','google_news','alpha_vantage','reference',
                  'macro','edgar_fts','rns','eqs','news_sitemap','gdelt','ai_search',
                  'tdnet','hkex','cninfo','dart','gazette','filings'));
