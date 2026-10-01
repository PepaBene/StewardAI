-- Rollback for supabase/migrations/20261001100100_macro_cnb_source.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- No row data is lost; only schema objects and seed rows the migration added are removed.

alter table public.macro_series drop constraint if exists macro_series_source_check;
alter table public.macro_series add constraint macro_series_source_check CHECK ((source = ANY (ARRAY['fred'::text, 'ecb'::text, 'eurostat'::text, 'cnb'::text, 'world_bank'::text, 'oecd'::text, 'bis'::text, 'un_comtrade'::text])));
