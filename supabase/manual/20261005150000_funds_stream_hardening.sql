-- Run in the Supabase SQL editor on the live project, ONE transaction (all or nothing).
-- Identical to supabase/migrations/20261005150000_funds_stream_hardening.sql, wrapped in begin/commit.
begin;

-- Funds / ownership hardening (audit B34, B39, C-lows, UK PDMR persistence).
--
-- - insider_filing_ticker      filing -> held ticker link: several tickers share one issuer CIK
--                              (dual-class), but insider_filing keeps one `ticker` per accession.
-- - fund_factsheet             insert-only versions (a re-fetch with different figures adds a row
--                              instead of rewriting history; point-in-time reads use fetched_at)
--                              and the snapshot FK no longer cascades: pruning a holdings snapshot
--                              keeps the factsheet history.
-- - control_stake_skipped_filing  13D/G accessions already downloaded and skipped (not held,
--                              unstructured, unparseable) so they are not re-downloaded each run.
-- - nport_ignored_filing       N-PORT filings of a registrant's other series (not downloaded again).
-- - pdmr_notification / pdmr_transaction  parsed UK MAR Article 19 notifications and the
--                              honest outcome of every retrieval attempt.

-- ---------------------------------------------------------------------------------------
-- B34 filing -> ticker link
-- ---------------------------------------------------------------------------------------
create table public.insider_filing_ticker (
  accession text not null references public.insider_filing (accession) on delete cascade,
  ticker    text not null,
  linked_at timestamptz not null default now(),
  primary key (accession, ticker)
);
create index insider_filing_ticker_ticker on public.insider_filing_ticker (ticker);

insert into public.insider_filing_ticker (accession, ticker)
select accession, ticker from public.insider_filing
on conflict do nothing;
-- Every other held ticker that shares the filing's issuer CIK (dual-class listings).
insert into public.insider_filing_ticker (accession, ticker)
select f.accession, i.t212_ticker
from public.insider_filing f
join public.instrument i on i.cik is not null and lpad(i.cik::text, 10, '0') = f.issuer_cik
on conflict do nothing;

-- ---------------------------------------------------------------------------------------
-- B39 / C-low fund_factsheet: insert-only versions, history survives snapshot pruning
-- ---------------------------------------------------------------------------------------
alter table public.fund_factsheet
  drop constraint fund_factsheet_fund_isin_as_of_source_key;
create index fund_factsheet_version
  on public.fund_factsheet (fund_isin, as_of, source, fetched_at desc, id desc);
create index fund_factsheet_known
  on public.fund_factsheet (fund_isin, fetched_at desc, id desc);
alter table public.fund_factsheet
  drop constraint fund_factsheet_snapshot_id_fkey,
  add constraint fund_factsheet_snapshot_id_fkey
    foreign key (snapshot_id) references public.etf_snapshot (id) on delete set null;

-- ---------------------------------------------------------------------------------------
-- C-low control stakes: accessions already looked at and skipped
-- ---------------------------------------------------------------------------------------
create table public.control_stake_skipped_filing (
  accession       text primary key check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  form            text not null,
  filed           date not null,
  reason          text not null check (reason in ('unstructured', 'not_held', 'parse_failed')),
  detail          text,
  issuer_cik      text check (issuer_cik is null or issuer_cik ~ '^[0-9]{10}$'),
  issuer_cusip    text check (issuer_cusip is null or issuer_cusip ~ '^[0-9A-Z]{9}$'),
  attempts        integer not null default 1,
  first_seen_at   timestamptz not null default now(),
  last_checked_at timestamptz not null default now()
);
create index control_stake_skipped_filing_reason
  on public.control_stake_skipped_filing (reason, last_checked_at);

-- ---------------------------------------------------------------------------------------
-- B40 N-PORT filings of a registrant's other series, seen and ignored
-- ---------------------------------------------------------------------------------------
create table public.nport_ignored_filing (
  accession         text primary key,
  registrant_cik    text,
  series_id         text,
  report_period_end date,
  filed             date,
  noted_at          timestamptz not null default now()
);

-- ---------------------------------------------------------------------------------------
-- UK PDMR notifications (MAR Article 19) from the RNS announcement stream
-- ---------------------------------------------------------------------------------------
create table public.pdmr_notification (
  id              bigint generated always as identity primary key,
  source_url      text not null unique,
  news_item_id    bigint references public.news_item (id) on delete set null,
  ticker          text,
  headline        text,
  announced_at    timestamptz,
  -- parsed: the template was read; the others record why nothing was read.
  status          text not null check (status in (
                    'parsed', 'no_template', 'access_unverified', 'paid_only',
                    'quota_exhausted', 'temporarily_failed')),
  detail          text,
  attempts        integer not null default 1,
  person_name     text,
  position        text,
  is_amendment    boolean,
  issuer_name     text,
  lei             text,
  first_attempt_at timestamptz not null default now(),
  last_attempt_at  timestamptz not null default now(),
  parsed_at       timestamptz
);
create index pdmr_notification_news_item on public.pdmr_notification (news_item_id)
  where news_item_id is not null;
create index pdmr_notification_ticker on public.pdmr_notification (ticker, announced_at desc);
create index pdmr_notification_status on public.pdmr_notification (status, last_attempt_at);

create table public.pdmr_transaction (
  notification_id bigint not null references public.pdmr_notification (id) on delete cascade,
  row_index       integer not null check (row_index >= 0),
  instrument      text,
  isin            text check (isin is null or isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  nature          text,
  price           numeric,
  currency        text,
  volume          numeric,
  transaction_date date,
  venue           text,
  economic_class  text not null check (economic_class in (
                    'open_market_buy', 'open_market_sell', 'option_exercise', 'grant_award',
                    'gift', 'tax_withholding', 'conversion', 'other')),
  classification_basis text not null,
  primary key (notification_id, row_index)
);

alter table public.insider_filing_ticker enable row level security;
alter table public.control_stake_skipped_filing enable row level security;
alter table public.pdmr_notification enable row level security;
alter table public.pdmr_transaction enable row level security;
alter table public.nport_ignored_filing enable row level security;
revoke all on public.insider_filing_ticker, public.control_stake_skipped_filing,
  public.pdmr_notification, public.pdmr_transaction, public.nport_ignored_filing
  from anon, authenticated;
grant select on public.insider_filing_ticker, public.control_stake_skipped_filing,
  public.pdmr_notification, public.pdmr_transaction, public.nport_ignored_filing
  to authenticated;
create policy owner_read on public.nport_ignored_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.insider_filing_ticker
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.control_stake_skipped_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.pdmr_notification
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.pdmr_transaction
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.insider_filing_ticker,
  public.control_stake_skipped_filing, public.pdmr_notification,
  public.pdmr_transaction, public.nport_ignored_filing to app_backend;
create policy backend_all on public.nport_ignored_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.insider_filing_ticker
  for all to app_backend using (true) with check (true);
create policy backend_all on public.control_stake_skipped_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.pdmr_notification
  for all to app_backend using (true) with check (true);
create policy backend_all on public.pdmr_transaction
  for all to app_backend using (true) with check (true);

comment on table public.insider_filing_ticker is
  'Which held tickers a Form 3/4/5 applies to: several tickers can share one issuer CIK.';
comment on table public.control_stake_skipped_filing is
  'Schedule 13D/G accessions downloaded once and skipped (not held, unstructured, unparseable).';
comment on table public.pdmr_notification is
  'UK PDMR announcements: the parsed notification, or the honest reason nothing was read.';

commit;
