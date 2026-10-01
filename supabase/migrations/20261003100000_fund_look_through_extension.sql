-- Phase 6 (D6.1, D6.4, D6.5, D6.8, D6.9): fund factsheets, snapshot retention pins, the
-- fund/index coverage ledger and the fund-characteristics parser-drift audit.
--
-- - fund_factsheet        per fund and holdings as-of date: share class, total assets, expense
--                         ratio, charges, benchmark, distribution policy, NAV basis, the
--                         cash / derivative / other remainder and whether the published rows
--                         reconcile to 100 %. Unknown stays NULL (never zero).
-- - fund_snapshot_pin     a holdings snapshot a saved report or decision refers to; the
--                         retention policy never prunes a pinned snapshot (ON DELETE RESTRICT
--                         makes the database refuse it too).
-- - fund_index_coverage   per fund or index: where its constituents come from (official
--                         holdings | issuer downloadable data | regulatory filing | proxy fund
--                         | unavailable) and whether total-return and historical-constituent
--                         data exist, so backtests can be flagged before use.
-- - fund_characteristic_audit  one row per observation of an issuer's fund-level P/E and P/B
--                         with the payload shape fingerprint and the verdict that separates a
--                         real value change from parser drift.

create table public.fund_factsheet (
  id                       bigint      generated always as identity primary key,
  fund_isin                text        not null check (fund_isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  as_of                    date        not null,
  source                   text        not null
                                       check (source in ('ishares_json', 'ishares_spreadsheet',
                                                         'vanguard_graphql', 'manual')),
  snapshot_id              bigint      references public.etf_snapshot (id) on delete cascade,
  provider                 text        check (provider in ('ishares', 'vanguard')),
  share_class              text,
  fund_name                text,
  fund_currency            text        check (fund_currency is null or fund_currency ~ '^[A-Z]{3}$'),
  total_assets             numeric     check (total_assets is null or total_assets >= 0),
  total_assets_currency    text        check (total_assets_currency is null
                                              or total_assets_currency ~ '^[A-Z]{3}$'),
  total_assets_as_of       date,
  -- Fraction per year (0.0007 = 0.07 %).
  expense_ratio            numeric     check (expense_ratio is null
                                              or (expense_ratio >= 0 and expense_ratio < 0.2)),
  charges                  jsonb       not null default '{}'::jsonb,
  benchmark_name           text,
  distribution_policy      text        check (distribution_policy is null
                                              or distribution_policy in ('accumulating',
                                                                         'distributing')),
  nav_basis                text,
  -- Weights as fractions of net assets, from the published rows.
  holdings_count           integer     not null default 0,
  equity_weight            numeric,
  cash_weight              numeric,
  derivative_weight        numeric,
  other_weight             numeric,
  total_weight             numeric,
  -- 1 - total_weight: fund weight the rows do not account for. Never redistributed.
  residual_weight          numeric,
  unclassified_sector_weight  numeric,
  unclassified_country_weight numeric,
  reconcile_tolerance      numeric     not null default 0.01,
  reconciles               boolean,
  -- How the constituents were obtained: the fund's own holdings or a stand-in for an index.
  lookthrough_basis        text        not null default 'issuer_holdings'
                                       check (lookthrough_basis in ('issuer_holdings',
                                                                    'issuer_spreadsheet',
                                                                    'index_proxy', 'manual')),
  coverage_note            text,
  missing_fields           text[]      not null default '{}',
  evidence                 jsonb       not null default '{}'::jsonb,
  fetched_at               timestamptz not null default now(),
  unique (fund_isin, as_of, source)
);
create index fund_factsheet_latest on public.fund_factsheet (fund_isin, as_of desc);
create index fund_factsheet_snapshot on public.fund_factsheet (snapshot_id)
  where snapshot_id is not null;

create table public.fund_snapshot_pin (
  id           bigint      generated always as identity primary key,
  snapshot_id  bigint      not null references public.etf_snapshot (id) on delete restrict,
  ref_kind     text        not null check (ref_kind in ('report', 'decision', 'manual')),
  ref_id       text        not null,
  reason       text,
  created_at   timestamptz not null default now(),
  unique (snapshot_id, ref_kind, ref_id)
);

create table public.fund_index_coverage (
  subject_kind          text        not null check (subject_kind in ('fund', 'index')),
  subject_key           text        not null check (length(subject_key) between 1 and 80),
  label                 text        not null,
  tier                  text        not null
                                    check (tier in ('official_holdings', 'issuer_downloadable',
                                                    'regulatory_filing', 'proxy_fund',
                                                    'unavailable')),
  origin                text        not null default 'derived'
                                    check (origin in ('derived', 'manual')),
  proxy_fund_isin       text        check (proxy_fund_isin is null
                                           or proxy_fund_isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  -- Unknown (NULL) is not "no": the backtest gate flags both.
  total_return_available      boolean,
  historical_constituents     boolean,
  latest_holdings_as_of       date,
  reason                text        not null,
  source_url            text,
  evidence              jsonb       not null default '{}'::jsonb,
  checked_at            timestamptz not null default now(),
  primary key (subject_kind, subject_key)
);

create table public.fund_characteristic_audit (
  id                bigint      generated always as identity primary key,
  fund_key          text        not null check (fund_key ~ '^[A-Za-z0-9._-]{1,32}$'),
  provider          text        not null check (provider in ('ishares', 'vanguard')),
  as_of             date        not null,
  fingerprint       text        not null,
  shape             jsonb       not null,
  rules             jsonb       not null default '{}'::jsonb,
  basis             text        not null default 'fund',
  pe                numeric,
  pb                numeric,
  index_pe          numeric,
  index_pb          numeric,
  verdict           text        not null
                                check (verdict in ('first_observation', 'unchanged',
                                                   'normal_update', 'value_change',
                                                   'methodology_change', 'parser_drift')),
  alert             boolean     not null default false,
  detail            jsonb       not null default '{}'::jsonb,
  observed_at       timestamptz not null default now()
);
create index fund_characteristic_audit_latest
  on public.fund_characteristic_audit (fund_key, observed_at desc, id desc);

comment on table public.fund_factsheet is
  'Issuer factsheet metadata and the cash/derivative/other remainder of a holdings snapshot.';
comment on column public.fund_factsheet.residual_weight is
  'Fund weight not accounted for by the published rows (1 - total_weight); unknown, never '
  'redistributed to known holdings.';
comment on table public.fund_snapshot_pin is
  'Holdings snapshots referenced by a saved report/decision: exempt from retention pruning.';
comment on table public.fund_index_coverage is
  'Where each fund''s/index''s constituents come from and whether performance/backtest use '
  'is safe (total return, historical constituents).';
comment on table public.fund_characteristic_audit is
  'Parser-drift audit of issuer fund-level P/E and P/B: shape fingerprint plus verdict.';

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.fund_factsheet enable row level security;
alter table public.fund_snapshot_pin enable row level security;
alter table public.fund_index_coverage enable row level security;
alter table public.fund_characteristic_audit enable row level security;

revoke all on public.fund_factsheet, public.fund_snapshot_pin, public.fund_index_coverage,
  public.fund_characteristic_audit from anon, authenticated;
grant select on public.fund_factsheet, public.fund_snapshot_pin, public.fund_index_coverage,
  public.fund_characteristic_audit to authenticated;

create policy owner_read on public.fund_factsheet
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.fund_snapshot_pin
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.fund_index_coverage
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.fund_characteristic_audit
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update, delete on public.fund_factsheet, public.fund_snapshot_pin,
  public.fund_index_coverage, public.fund_characteristic_audit to app_backend;

create policy backend_all on public.fund_factsheet
  for all to app_backend using (true) with check (true);
create policy backend_all on public.fund_snapshot_pin
  for all to app_backend using (true) with check (true);
create policy backend_all on public.fund_index_coverage
  for all to app_backend using (true) with check (true);
create policy backend_all on public.fund_characteristic_audit
  for all to app_backend using (true) with check (true);
