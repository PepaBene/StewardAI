-- Rollback for supabase/migrations/20261006140000_ticker_case_insensitive_indexes.sql.
-- Only expression indexes are dropped; no table, column or data changes, so nothing is lost and
-- lookups simply fall back to a sequential scan. Run in a transaction, newest migration first.
-- Proven by apps/backend/tests/test_funds_ownership_fixpass.py::test_ticker_lookups_use_the_functional_indexes
-- and the up/down round trip in test_lifecycle_phase8.py.
drop index if exists public.insider_filing_ticker_link_lower;
drop index if exists public.insider_filing_ticker_lower;
drop index if exists public.pdmr_notification_ticker_lower;
