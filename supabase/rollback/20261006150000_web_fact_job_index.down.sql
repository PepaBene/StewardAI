-- Rollback for supabase/migrations/20261006150000_web_fact_job_index.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- No data is lost: it only drops the index on web_fact.job_id.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database to run it.
drop index if exists public.web_fact_job_id_idx;
