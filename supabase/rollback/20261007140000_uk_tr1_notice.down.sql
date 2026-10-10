-- Rollback for supabase/migrations/20261007140000_uk_tr1_notice.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops every stored UK TR-1 notice and its review status.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

drop table if exists public.uk_tr1_notice;
