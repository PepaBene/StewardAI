-- Rollback for supabase/migrations/20261007160000_deal_events.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops deal_event, deal_participant, deal_state_transition, deal_term_version,
-- deal_document and deal_structure_link, so every recorded deal, its state ledger, every term
-- version and every closing link is lost. Nothing else references them. The source documents and
-- evidence rows they pointed at are untouched.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

drop table if exists public.deal_structure_link;
drop table if exists public.deal_document;
drop table if exists public.deal_term_version;
drop table if exists public.deal_state_transition;
drop table if exists public.deal_participant;
drop table if exists public.deal_event;
