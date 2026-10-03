-- Rollback for supabase/migrations/20261006140000_ticker_case_insensitive_indexes.sql.
drop index if exists public.insider_filing_ticker_link_lower;
drop index if exists public.insider_filing_ticker_lower;
drop index if exists public.pdmr_notification_ticker_lower;
