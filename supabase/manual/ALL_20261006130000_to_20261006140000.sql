-- Migrations 20261006130000 .. 20261006140000 in ONE transaction (all or nothing).
-- Run in the Supabase SQL editor on the live project AFTER the 20261005 scripts. Registering them in
-- supabase_migrations.schema_migrations is left to `supabase db push`/the reconcile script.
begin;

-- ===== 20261006130000_patent_forward_citations_nullable.sql =====
-- F5-06: a family whose source cannot supply forward citations (EPO OPS bibliographic data)
-- stores NULL ("unknown"), not 0 ("no one cites it"). Additive: the column loses NOT NULL and
-- its default; existing readers coalesce. Rows from the epo_ops source stored 0 only because
-- OPS cannot provide the figure (raw.forward_citations says so), so those zeros become NULL.
-- Rows from any other source keep their value.

alter table public.patent_family alter column citations_forward drop not null;
alter table public.patent_family alter column citations_forward drop default;
update public.patent_family set citations_forward = null
  where source = 'epo_ops' and citations_forward = 0;

-- ===== 20261006140000_ticker_case_insensitive_indexes.sql =====
-- Case-insensitive ticker lookups for the PDMR and insider routes (audit F6-06).
--
-- T212 UK/EU tickers are stored with a lowercase exchange suffix ('RRl_EQ') while the routes used
-- to upper-case the requested ticker. The routes now compare lower(ticker) = lower($1); these
-- expression indexes keep that an index scan. No data is rewritten; the existing btree indexes
-- stay for exact-case callers. Tables keep their existing owner-only RLS and app_backend grants.

create index if not exists pdmr_notification_ticker_lower
  on public.pdmr_notification (lower(ticker), announced_at desc);
create index if not exists insider_filing_ticker_lower
  on public.insider_filing (lower(ticker));
create index if not exists insider_filing_ticker_link_lower
  on public.insider_filing_ticker (lower(ticker));

commit;
