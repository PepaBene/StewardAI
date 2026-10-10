-- Rollback for supabase/migrations/20261024130000_holding_risk_tier.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the stored risk tier of each holding (they are worked out again on the next sync).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.holding_risk_tier;
