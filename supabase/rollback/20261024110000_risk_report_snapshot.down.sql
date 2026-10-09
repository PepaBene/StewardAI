-- Rollback for supabase/migrations/20261024110000_risk_report_snapshot.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every saved risk report. They can be recomputed only with today's model.
-- Written by hand and not yet proven against a database.

drop table if exists public.risk_report_snapshot;
