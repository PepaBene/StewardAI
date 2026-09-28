-- SEC XBRL: shares outstanding (the denominator of 13F "% of shares outstanding") and
-- revenue by region / segment / product, read by the `sec_xbrl` job.
--
-- - shares_outstanding: one row per holding and source fact(s). `method` records which step
--   of the fallback chain produced it: the cover-page dei fact in companyfacts, the dei facts
--   of the latest filing's XBRL instance (summed over share classes when tagged per class),
--   the balance-sheet us-gaap / ifrs-full shares outstanding, or a value entered manually in
--   the holding profile. `raw` keeps the source facts exactly as the SEC served them (the
--   companyfacts JSON entries, or the instance's fact and context XML).
-- - xbrl_filing: one parsed filing instance (10-K, 10-Q, 20-F); the instance and label
--   linkbase URLs point at the SEC archive, which never changes a filed document.
-- - segment_revenue: the revenue facts of an instance whose context has exactly one explicit
--   member on a geographic, business-segment or product axis (cross-tabs are skipped), and
--   the undimensioned total (kind 'total'). Labels are the filing's own ("as reported").

create table public.shares_outstanding (
  id          bigint      generated always as identity primary key,
  ticker      text        not null,
  cik         text        check (cik ~ '^[0-9]{10}$'),
  shares      numeric     not null check (shares > 0),
  as_of       date        not null,
  method      text        not null check (method in (
                'dei_companyfacts', 'dei_instance', 'dei_instance_classes',
                'us_gaap_common_stock', 'ifrs_number_of_shares', 'manual')),
  -- Per share class when the figure sums or selects class-tagged facts:
  -- [{"member": "us-gaap:CommonClassAMember", "label": "Class A", "shares": 5868000000,
  --   "counted": true}].
  classes     jsonb       not null default '[]'::jsonb,
  accession   text        check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  form        text,
  filed       date,
  source_url  text,
  -- For 20-F filers: the ADS ratio the filing states ("each ADS represents five (5) common
  -- shares"), with the sentence it was read from; null when not found or not looked for.
  ads_ratio_reported numeric check (ads_ratio_reported > 0),
  ads_ratio_text     text,
  raw         jsonb       not null,
  job_id      bigint      references public.job (id),
  fetched_at  timestamptz not null default now()
);

create unique index shares_outstanding_source
  on public.shares_outstanding (ticker, method, as_of, coalesce(accession, ''));
create index shares_outstanding_latest on public.shares_outstanding (ticker, fetched_at desc);

create table public.xbrl_filing (
  id              bigint      generated always as identity primary key,
  ticker          text        not null,
  cik             text        not null check (cik ~ '^[0-9]{10}$'),
  accession       text        not null check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  form            text        not null,
  filed           date        not null,
  report_date     date,
  instance_url    text        not null,
  label_url       text,
  facts_seen      integer     not null default 0,
  rows_kept       integer     not null default 0,
  skipped_multi   integer     not null default 0,
  parser_version  integer     not null,
  job_id          bigint      references public.job (id),
  parsed_at       timestamptz not null default now(),
  unique (ticker, accession)
);

create table public.segment_revenue (
  id            bigint      generated always as identity primary key,
  filing_id     bigint      not null references public.xbrl_filing (id) on delete cascade,
  kind          text        not null check (kind in ('geographic', 'segment', 'product', 'total')),
  axis          text,
  member        text,
  label         text        not null,
  label_source  text        not null check (label_source in ('filing', 'derived')),
  concept       text        not null,
  period_start  date        not null,
  period_end    date        not null check (period_end >= period_start),
  value         numeric     not null,
  unit          text        not null,
  decimals      text,
  subtotal      boolean     not null default false,
  context_id    text        not null,
  fact_id       text,
  check ((kind = 'total') = (axis is null and member is null))
);

create index segment_revenue_filing on public.segment_revenue (filing_id, kind, period_end);

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.shares_outstanding enable row level security;
alter table public.xbrl_filing enable row level security;
alter table public.segment_revenue enable row level security;

revoke all on public.shares_outstanding, public.xbrl_filing, public.segment_revenue
  from anon, authenticated;
grant select on public.shares_outstanding, public.xbrl_filing, public.segment_revenue
  to authenticated;

create policy owner_read on public.shares_outstanding
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.xbrl_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.segment_revenue
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update, delete on public.shares_outstanding, public.xbrl_filing,
  public.segment_revenue to app_backend;

create policy backend_all on public.shares_outstanding
  for all to app_backend using (true) with check (true);
create policy backend_all on public.xbrl_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.segment_revenue
  for all to app_backend using (true) with check (true);
