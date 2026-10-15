-- Rollback for supabase/migrations/20261109100000_autopilot.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the autopilot ledger is dropped, which is every decision the app made on its own
-- with its evidence and revert payloads, and the modes calibration set. What the decisions
-- changed (confirmed facts, edges, listings, config versions) stays; it just can no longer be
-- reverted from the ledger.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.autopilot_kind_state;
drop table if exists public.auto_decision;
