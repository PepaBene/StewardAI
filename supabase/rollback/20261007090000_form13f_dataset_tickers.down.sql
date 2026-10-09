-- Rollback for supabase/migrations/20261007090000_form13f_dataset_tickers.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops form13f_dataset.tickers, so the 13F job forgets which holdings each data set
-- was searched for and reads the data sets again.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

alter table public.form13f_dataset drop column if exists tickers;
