-- Rollback for supabase/migrations/20261028110000_portfolio_history_reconstructed.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the two reconstructed tables are dropped with their rows. They are rebuilt from the
-- ledger by the returns_refresh job, and account_snapshot and position_snapshot are untouched.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop view if exists public.position_history;
drop view if exists public.account_history;
drop table if exists public.position_snapshot_reconstructed;
drop table if exists public.account_snapshot_reconstructed;
