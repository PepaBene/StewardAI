-- Rollback for supabase/migrations/20261025100000_fund_nav_origin.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the issuer/owner marker on every NAV point is dropped; afterwards the issuer sync
-- cannot tell its own rows from owner entries again.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
alter table public.fund_nav_point drop column if exists origin;
