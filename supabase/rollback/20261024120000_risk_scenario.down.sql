-- Rollback for supabase/migrations/20261024120000_risk_scenario.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the saved what-if scenarios.
-- Written by hand and not yet proven against a database.

drop table if exists public.risk_scenario;
