-- Rollback for supabase/migrations/20261007100000_discovery_universe_screens.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every saved screen, screen version, screen run, column set and universe
-- snapshot. Later migrations that reference these tables must be rolled back first.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop view if exists public.investment_universe;
drop table if exists public.screen_run;
drop table if exists public.screen_definition_version;
drop table if exists public.screen_definition;
drop table if exists public.screen_column_set;
drop table if exists public.universe_snapshot_member;
drop table if exists public.universe_snapshot;
drop function if exists private.screen_version_immutable();
