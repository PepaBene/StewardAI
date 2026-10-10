-- Rollback for supabase/migrations/20261012100000_issuer_benchmark_series.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every price_bar row whose source is 'issuer' (the stored benchmark series).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

delete from public.price_bar where source = 'issuer';
alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar
  add constraint price_bar_source_check
  check (source in ('stooq', 'yahoo', 'eodhd', 'alpha_vantage', 'twelve_data'));

comment on column public.price_bar.source is
  'stooq (source of record), yahoo (unofficial chart API), eodhd and alpha_vantage (gap '
  'fallbacks under free-tier budgets), twelve_data (reserved).';
