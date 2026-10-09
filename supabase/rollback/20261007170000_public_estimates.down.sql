-- Rollback for supabase/migrations/20261007170000_public_estimates.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops consensus_source, public_estimate_snapshot, estimate_value and
-- estimate_pair_review, so every stored consensus/guidance/named estimate, the registry of
-- consensus files and every reviewed revision pair is lost; the source documents and evidence the
-- values cite are not touched. The revision-alert gate then restarts from zero reviewed pairs.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

drop table if exists public.estimate_pair_review;
drop trigger if exists estimate_value_series_guard on public.estimate_value;
drop table if exists public.estimate_value;
drop function if exists public.estimate_value_series_guard();
drop table if exists public.public_estimate_snapshot;
drop table if exists public.consensus_source;
