-- Rollback for supabase/migrations/20261002130000_source_type_reference.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- No row data is lost; only schema objects and seed rows the migration added are removed.

alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check CHECK ((type = ANY (ARRAY['rss'::text, 'page'::text, 'edgar'::text, 'finnhub'::text, 'google_news'::text, 'alpha_vantage'::text, 'macro'::text, 'edgar_fts'::text, 'rns'::text, 'eqs'::text])));
