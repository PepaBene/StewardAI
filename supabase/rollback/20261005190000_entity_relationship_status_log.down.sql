-- Rollback for supabase/migrations/20261005190000_entity_relationship_status_log.sql (plan D8.4).
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every graph_status_log row for entity_relationship (the baseline entries and
-- every status change recorded since the migration).
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop trigger if exists entity_relationship_status_log on public.entity_relationship;
drop function if exists public.entity_relationship_log_status();
delete from public.graph_status_log where table_name = 'entity_relationship';
