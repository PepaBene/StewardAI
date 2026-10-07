-- Rollback for supabase/migrations/20261007090000_form13f_dataset_tickers.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: forgets which holdings each 13F data set was searched for, so the next 13F run
-- reads every loaded data set again for all holdings.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

alter table public.form13f_dataset drop column if exists tickers;
