-- Rollback for supabase/migrations/20261005130000_fund_statement_store.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: issuer_ir_source.
-- DATA LOSS: deletes fundamentals rows with source 'alphavantage' (a cache of the Company
-- Overview), financial_statement_fact rows that hold only value_text, and drops the columns
-- cik, value_text, line_item, accepted_at, amends_accession, is_amendment,
-- forward_observation.observation_key, peer_set.status_history and the IR version columns.

delete from public.fundamentals where source = 'alphavantage';
alter table public.fundamentals drop constraint if exists fundamentals_source_check;
alter table public.fundamentals add constraint fundamentals_source_check
  check (source in ('sec', 'esef', 'finnhub'));

drop index if exists public.financial_fact_filing;
drop index if exists public.financial_fact_line_item;
delete from public.financial_statement_fact where value is null and text_block is null;
alter table public.financial_statement_fact
  drop constraint if exists financial_statement_fact_content_check;
alter table public.financial_statement_fact
  drop column if exists cik,
  drop column if exists value_text,
  drop column if exists line_item,
  drop column if exists accepted_at,
  drop column if exists amends_accession,
  drop column if exists is_amendment;
alter table public.financial_statement_fact add constraint financial_statement_fact_check
  check (value is not null or text_block is not null);

drop trigger if exists forward_observation_set_key on public.forward_observation;
drop function if exists public.forward_observation_set_key();
drop index if exists public.forward_observation_content;
alter table public.forward_observation drop column if exists observation_key;

alter table public.peer_set drop column if exists status_history;

drop index if exists public.issuer_ir_release_source;
drop index if exists public.issuer_ir_release_previous;
alter table public.issuer_ir_release
  drop column if exists source_id,
  drop column if exists previous_release_id,
  drop column if exists content_type,
  drop column if exists byte_size,
  drop column if exists fetched_at;
drop table if exists public.issuer_ir_source cascade;

drop index if exists public.xbrl_filing_job;
drop index if exists public.shares_outstanding_job;
