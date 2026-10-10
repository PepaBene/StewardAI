-- Rollback for supabase/migrations/20261025100000_switch_log.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every switch the owner logged; the log is append-only and cannot be rebuilt.
-- Written by hand and not yet proven against a database.

drop table if exists public.switch_log;
