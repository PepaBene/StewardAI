-- Rollback for supabase/migrations/20261006140000_filing_extraction.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the record of which filings were read, so every annual report is read
-- again by the next extraction run, and the memory of exposure rows the owner deleted is lost.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.filing_extraction;
