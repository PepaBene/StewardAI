-- Rollback for supabase/migrations/20261008120000_deal_party_decisions.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every confirmation and rejection of a deal party's issuer match; the
-- convenience copy on deal_participant.issuer_id is untouched.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.deal_participant_decision;
