-- Rollback for supabase/migrations/20261001140000_intraday_prices.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: intraday_bar, market_payload, market_quote.
-- DATA LOSS: drops these columns and their values: price_bar.currency.

alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar drop column if exists currency cascade;
drop table if exists public.intraday_bar cascade;
drop table if exists public.market_payload cascade;
drop table if exists public.market_quote cascade;
