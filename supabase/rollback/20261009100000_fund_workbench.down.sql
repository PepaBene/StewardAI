-- Rollback for supabase/migrations/20261009100000_fund_workbench.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every owner-entered fund fact.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.fund_owner_fact;
