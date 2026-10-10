-- Rollback for supabase/migrations/20261013100000_portfolio_performance.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the owner's saved performance settings from before they became a config
-- domain (20261014100000); roll that migration back first.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.performance_setting;
