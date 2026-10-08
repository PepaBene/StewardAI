-- Rollback for supabase/migrations/20261017100000_xray_owner_input.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every owner-entered regional split, supply-chain edge and index weight list.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.xray_owner_input;
