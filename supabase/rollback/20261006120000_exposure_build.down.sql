-- Rollback for supabase/migrations/20261006120000_exposure_build.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the build log, and the origin and provenance of every exposure mapping,
-- so built rows can no longer be told from the owner's own rows afterwards.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.exposure_build_log;
alter table public.exposure_mapping drop column if exists provenance, drop column if exists origin;
