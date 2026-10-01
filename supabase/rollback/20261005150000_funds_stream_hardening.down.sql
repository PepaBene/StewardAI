-- Rollback for supabase/migrations/20261005150000_funds_stream_hardening.sql.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (catalog fingerprint equals the one taken
-- before the migration). Run it in a transaction, newest migration first. The migration file
-- itself is never edited.
-- DATA LOSS: drops these tables and every row in them: control_stake_skipped_filing, insider_filing_ticker, nport_ignored_filing, pdmr_notification, pdmr_transaction.
-- DATA LOSS: fund_factsheet keeps only the newest version per (fund_isin, as_of, source); older versions are deleted so the unique constraint can return, and rows whose snapshot was pruned lose nothing but the cascade they are exempt from today.

drop table if exists public.pdmr_transaction;
drop table if exists public.pdmr_notification;
drop table if exists public.control_stake_skipped_filing;
drop table if exists public.insider_filing_ticker;
drop table if exists public.nport_ignored_filing;

delete from public.fund_factsheet f
using public.fund_factsheet newer
where newer.fund_isin = f.fund_isin and newer.as_of = f.as_of and newer.source = f.source
  and (newer.fetched_at, newer.id) > (f.fetched_at, f.id);
drop index if exists public.fund_factsheet_version;
drop index if exists public.fund_factsheet_known;
alter table public.fund_factsheet
  add constraint fund_factsheet_fund_isin_as_of_source_key unique (fund_isin, as_of, source);
alter table public.fund_factsheet
  drop constraint fund_factsheet_snapshot_id_fkey,
  add constraint fund_factsheet_snapshot_id_fkey
    foreign key (snapshot_id) references public.etf_snapshot (id) on delete cascade;
