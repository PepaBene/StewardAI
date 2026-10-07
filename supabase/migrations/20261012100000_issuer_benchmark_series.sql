-- Benchmark series the fund issuer publishes (iShares performance chart, rebased to 10,000).
--   price_bar.source + 'issuer': kept apart from the market-data sources so a series never mixes.
alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar
  add constraint price_bar_source_check
  check (source in ('stooq', 'yahoo', 'eodhd', 'alpha_vantage', 'twelve_data', 'issuer'));

comment on column public.price_bar.source is
  'stooq (source of record), yahoo (unofficial chart API), eodhd and alpha_vantage (gap '
  'fallbacks under free-tier budgets), twelve_data (reserved), issuer (a fund issuer''s '
  'published benchmark series, rebased to 10,000; not a market quote).';
