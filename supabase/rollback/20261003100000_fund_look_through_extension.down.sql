-- Rollback for supabase/migrations/20261003100000_fund_look_through_extension.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: fund_characteristic_audit, fund_factsheet, fund_index_coverage, fund_snapshot_pin.

drop table if exists public.fund_characteristic_audit cascade;
drop table if exists public.fund_factsheet cascade;
drop table if exists public.fund_index_coverage cascade;
drop table if exists public.fund_snapshot_pin cascade;
