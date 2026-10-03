-- Rollback for supabase/migrations/20261007171000_price_bar_pse_source.sql (plan D8.4).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (catalog fingerprint before the migration
-- equals the one after this file) and apps/backend/tests/test_browser_pass_px.py (a stored 'pse'
-- close is refused once this file has run). Run it in a transaction, newest migration first. The
-- migration file itself is never edited.
-- DATA LOSS: deletes every price_bar row with source 'pse' (the PX closes read off the PSE page;
-- PSE publishes no history, so they cannot be re-read for past sessions).

delete from public.price_bar where source = 'pse';
alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar
  add constraint price_bar_source_check
  check (source in ('stooq', 'yahoo', 'eodhd', 'alpha_vantage', 'twelve_data'));

comment on column public.price_bar.source is
  'stooq (source of record), yahoo (unofficial chart API), eodhd and alpha_vantage (gap '
  'fallbacks under free-tier budgets), twelve_data (reserved).';
