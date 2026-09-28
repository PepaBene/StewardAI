-- ETF look-through: what each fund the portfolio holds (or that stands in for a benchmark)
-- itself holds, from the issuers' own (undocumented) data endpoints.
--
--   etf_snapshot   one row per fund, as-of date and source: the ORIGINAL payloads as the
--                  issuer returned them (JSON documents in `raw`, the SpreadsheetML text in
--                  `raw_text`), plus what was read off them.
--   etf_holding    the normalised holding rows of a snapshot: name, identifiers, weight,
--                  sector (as published and normalised to a GICS-style sector), country
--                  (ISO code; `etf_snapshot.country_basis` says whether it is the country of
--                  risk or the listing country), currency (the issuer's field, or derived
--                  from the listing country: `currency_derived`).
--   etf_breakdown  fund-level tables the issuer publishes (sector, country, region), with
--                  the benchmark index's weight where the issuer gives it (Vanguard's
--                  `benchmarkMktPercent`: the FTSE All-World weights).
--
-- A snapshot is written once per (fund, as-of date, source); the weekly job skips the
-- download when the as-of date has not moved. Gaps (an endpoint answering 400, a missing
-- container) are recorded in `sync_state` ('etf_look_through_status') and on the job step,
-- never as a partial snapshot.
--
-- Same access model as every other table: RLS on, owner-only SELECT for the frontend,
-- `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

create table public.etf_snapshot (
  id               bigint      generated always as identity primary key,
  fund_isin        text        not null check (fund_isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  provider         text        not null check (provider in ('ishares', 'vanguard')),
  portfolio_id     text        not null,
  source           text        not null
                               check (source in ('ishares_json', 'ishares_spreadsheet',
                                                 'vanguard_graphql')),
  as_of            date        not null,
  fund_name        text,
  fund_currency    text,
  -- What `etf_holding.country` means for this snapshot.
  country_basis    text        check (country_basis in ('country_of_risk', 'listing_country')),
  -- 'issuer': the issuer's own per-holding currency field; 'derived': mapped from the
  -- listing country (Vanguard publishes no currency).
  currency_basis   text        check (currency_basis in ('issuer', 'derived')),
  holdings_count   integer     not null default 0,
  -- Sum of the equity rows' weights (the rest is cash, derivatives and other).
  equity_weight    numeric,
  warnings         text[]      not null default '{}',
  raw              jsonb,
  raw_text         text,
  fetched_at       timestamptz not null default now(),
  job_id           bigint      references public.job (id) on delete set null,
  unique (fund_isin, as_of, source),
  check (raw is not null or raw_text is not null)
);

create index etf_snapshot_latest on public.etf_snapshot (fund_isin, as_of desc, fetched_at desc);

create table public.etf_holding (
  snapshot_id      bigint      not null references public.etf_snapshot (id) on delete cascade,
  row_index        integer     not null,
  name             text,
  isin             text,
  ticker           text,
  sedol            text,
  asset_class      text        not null check (asset_class in ('equity', 'cash', 'derivative',
                                                             'other')),
  security_type    text,
  -- Fraction of the fund's net assets (0.0814 = 8.14 %).
  weight           numeric     not null,
  sector_raw       text,
  sector           text,
  country_raw      text,
  country          text        check (country ~ '^[A-Z]{2}$'),
  currency         text        check (currency ~ '^[A-Z]{3}$'),
  currency_derived boolean     not null default false,
  exchange         text,
  market_value     numeric,
  primary key (snapshot_id, row_index)
);

create index etf_holding_isin on public.etf_holding (isin) where isin is not null;

create table public.etf_breakdown (
  snapshot_id      bigint      not null references public.etf_snapshot (id) on delete cascade,
  dimension        text        not null check (dimension in ('sector', 'country', 'region')),
  -- The classification the table follows, e.g. 'ishares_sector', 'icb_industry',
  -- 'ftse_country', 'msci_country', 'ftse_region'.
  scheme           text        not null,
  code             text,
  label            text        not null,
  fund_weight      numeric,
  benchmark_weight numeric,
  as_of            date,
  primary key (snapshot_id, scheme, label)
);

comment on table public.etf_snapshot is
  'Issuer holdings snapshots per fund and as-of date (original payloads kept in raw/raw_text).';
comment on column public.etf_holding.currency_derived is
  'True when the currency was mapped from the listing country, not published by the issuer.';
comment on column public.etf_breakdown.benchmark_weight is
  'The benchmark index weight the issuer publishes next to the fund weight (fraction).';

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.etf_snapshot enable row level security;
alter table public.etf_holding enable row level security;
alter table public.etf_breakdown enable row level security;

revoke all on public.etf_snapshot, public.etf_holding, public.etf_breakdown
  from anon, authenticated;
grant select on public.etf_snapshot, public.etf_holding, public.etf_breakdown to authenticated;

create policy owner_read on public.etf_snapshot
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.etf_holding
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.etf_breakdown
  for select to authenticated using ((select private.is_owner()));

-- Backend: snapshots are written by the look-through job and pruned to the newest few per
-- fund (holdings and breakdowns go with their snapshot).
grant select, insert, update, delete on public.etf_snapshot, public.etf_holding,
  public.etf_breakdown to app_backend;

create policy backend_all on public.etf_snapshot
  for all to app_backend using (true) with check (true);
create policy backend_all on public.etf_holding
  for all to app_backend using (true) with check (true);
create policy backend_all on public.etf_breakdown
  for all to app_backend using (true) with check (true);
