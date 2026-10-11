-- Rollback for supabase/migrations/20261024110000_risk_report_snapshot.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every saved risk report. They can be recomputed only with today's model.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.risk_report_snapshot;
