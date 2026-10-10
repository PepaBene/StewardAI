-- Rollback for supabase/migrations/20261007150000_disclosure_alert_email.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: forgets which alerts were already e-mailed, so the next digest may repeat them.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

drop index if exists public.disclosure_alert_unmailed;
alter table public.disclosure_alert drop column if exists emailed_at;
