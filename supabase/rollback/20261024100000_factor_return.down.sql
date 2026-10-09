-- Rollback for supabase/migrations/20261024100000_factor_return.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the stored factor returns; the factor job downloads them again.
-- Written by hand and not yet proven against a database.

drop table if exists public.factor_return;
