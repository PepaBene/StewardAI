-- Rollback for supabase/migrations/20261006100000_ir_discovery_decision.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the log of investor-page discovery attempts (the pages it applied stay in
-- the holding_profiles config versions).
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.ir_discovery_decision;
