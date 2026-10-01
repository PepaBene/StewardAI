-- Rollback for supabase/migrations/20261001111937_price_bar_basis_key.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- No row data is lost; only schema objects and seed rows the migration added are removed.

alter table public.price_bar drop constraint if exists price_bar_pkey;
alter table public.price_bar add constraint price_bar_pkey PRIMARY KEY (symbol, date, source);
