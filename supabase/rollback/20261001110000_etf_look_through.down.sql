-- Rollback for supabase/migrations/20261001110000_etf_look_through.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: etf_breakdown, etf_holding, etf_snapshot.

drop table if exists public.etf_breakdown cascade;
drop table if exists public.etf_holding cascade;
drop table if exists public.etf_snapshot cascade;
