-- Rollback for supabase/migrations/20261006110000_web_fact.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every web fact (including owner decisions on them) and the search-attempt
-- log. A next report date that gap research handed to the earnings calendar stays on the
-- instrument (report_date_source 'web:<host>').
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.web_fact_attempt;
drop table if exists public.web_fact;
