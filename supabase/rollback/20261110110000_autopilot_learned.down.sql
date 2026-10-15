-- Rollback for supabase/migrations/20261110110000_autopilot_learned.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the learned confidence floors are dropped; each kind goes back to the Jobs config's
-- `min_confidence`. The next weekly calibration learns them again from the ledger.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.autopilot_learned;
