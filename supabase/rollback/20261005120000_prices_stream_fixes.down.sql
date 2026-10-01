-- Rollback for supabase/migrations/20261005120000_prices_stream_fixes.sql (plan D8.4).
-- Hand-written to the same standard as the generated ones and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: deletes price_bar rows of the sources eodhd, alpha_vantage and twelve_data (the older CHECK only admits stooq and yahoo).
-- DATA LOSS: drops these columns and their values: corporate_action.decided_at, corporate_action.decided_by, corporate_action.decision_reason, exchange_session.reason, price_return_series.calc_version, price_return_series.inputs_digest, security_listing.source, security_listing.evidence.
-- DATA LOSS: keeps only the newest calc_version row of each price_return_series key.
-- NOT RESTORED: the Stooq currency and quote_unit backfill (the values are correct and stay).

delete from public.price_bar where source not in ('stooq', 'yahoo');
alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar
  add constraint price_bar_source_check check (source in ('stooq', 'yahoo'));
comment on column public.price_bar.source is
  'stooq (source of record) or yahoo (Yahoo Finance chart API, unofficial; the fallback).';

drop index if exists public.corporate_action_symbol_ex_date;
alter table public.corporate_action drop constraint if exists corporate_action_stock_dividend_ratio;
alter table public.corporate_action
  drop column if exists decided_at,
  drop column if exists decided_by,
  drop column if exists decision_reason;
delete from public.corporate_action where currency in ('GBp', 'ZAc', 'ILa');
alter table public.corporate_action drop constraint if exists corporate_action_currency_check;
alter table public.corporate_action
  add constraint corporate_action_currency_check
  check (currency is null or currency ~ '^[A-Z]{3}$');

alter table public.exchange_session drop column if exists reason;
alter index if exists public.exchange_session_source rename to exchange_session_source_evidence;

delete from public.price_return_series a
 using public.price_return_series b
 where a.symbol = b.symbol and a.start_date = b.start_date and a.end_date = b.end_date
   and a.adjustment_basis = b.adjustment_basis and a.source = b.source
   and (a.calc_version, a.known_at, a.id) < (b.calc_version, b.known_at, b.id);
drop index if exists public.price_return_series_version_key;
alter table public.price_return_series
  drop column if exists calc_version,
  drop column if exists inputs_digest;
alter table public.price_return_series
  add unique (symbol, start_date, end_date, adjustment_basis, source);

drop index if exists public.provider_identifier_listing;
drop index if exists public.provider_identifier_natural_key;
drop index if exists public.security_listing_natural_key;
alter table public.security_listing
  drop column if exists source,
  drop column if exists evidence;

drop function if exists public.refund_provider_credits(text, text, numeric, timestamptz);
