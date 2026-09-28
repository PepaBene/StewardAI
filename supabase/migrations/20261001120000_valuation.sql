-- Valuation: per-holding fundamentals (EPS TTM, book value and dividends per share) and
-- fund-level valuation (P/E, P/B) of the benchmark ETFs, written by the `valuation_refresh`
-- job and read by GET /holdings/{ticker}/valuation and GET /portfolio/valuation.
--
-- - fundamentals: one row per holding and source, replaced on each successful fetch.
--   `figures` holds the per-share figures computed from the source (with the period, the
--   method and every input fact), `raw` the source's own facts that were used, verbatim
--   (SEC companyfacts rows, the filing instance's fact elements, ESEF xBRL-JSON fact
--   objects, Finnhub's metric object), so each figure can be traced back to its filing.
--   A failed refresh keeps the last good figures and records the error.
-- - fund_valuation: fund-level P/E and P/B as the provider publishes them (iShares product
--   page, Vanguard's product-data API), one row per fund and as-of date, with the raw
--   payload and the provider's stated methodology.
--
-- Prices are not copied: ratios are computed on read from `price_bar` and `fx_rate`.

create table public.fundamentals (
  ticker            text        not null check (ticker ~ '^[A-Za-z0-9._^-]{1,32}$'),
  source            text        not null check (source in ('sec', 'esef', 'finnhub')),
  -- Accounting basis of the figures: 'US GAAP', 'IFRS', or null (a vendor's own).
  basis             text,
  -- Currency of the per-share figures (e.g. USD for TSM's convenience translation).
  currency          text        check (currency ~ '^[A-Z]{3}$'),
  -- The filing (accession number, filings.xbrl.org id) or request the figures came from.
  document_ref      text,
  document_url      text        not null,
  period_end        date,
  figures           jsonb       not null,
  raw               jsonb       not null,
  fetched_at        timestamptz not null default now(),
  last_attempt_at   timestamptz not null default now(),
  last_error        text,
  primary key (ticker, source)
);

create table public.fund_valuation (
  fund_key          text        not null check (fund_key ~ '^[A-Za-z0-9._-]{1,32}$'),
  provider          text        not null check (provider in ('ishares', 'vanguard')),
  as_of             date        not null,
  pe                numeric     check (pe is null or pe > 0),
  pb                numeric     check (pb is null or pb > 0),
  -- The index's own figures, when the provider publishes them next to the fund's.
  index_pe          numeric     check (index_pe is null or index_pe > 0),
  index_pb          numeric     check (index_pb is null or index_pb > 0),
  -- Dividend yield of the fund's holdings as a fraction, and what kind it is.
  dividend_yield    numeric,
  dividend_yield_kind text      check (dividend_yield_kind in ('trailing', 'forecast')),
  methodology       text,
  source_url        text        not null,
  raw               jsonb       not null,
  fetched_at        timestamptz not null default now(),
  primary key (fund_key, as_of)
);

create index fund_valuation_latest on public.fund_valuation (fund_key, as_of desc);

comment on table public.fundamentals is
  'Per-holding fundamentals per source (valuation_refresh job): computed figures plus the '
  'verbatim source facts they came from.';
comment on table public.fund_valuation is
  'Fund-level P/E and P/B as published by the ETF provider, with its methodology and raw payload.';

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.fundamentals enable row level security;
alter table public.fund_valuation enable row level security;

revoke all on public.fundamentals, public.fund_valuation from anon, authenticated;
grant select on public.fundamentals, public.fund_valuation to authenticated;

create policy owner_read on public.fundamentals
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.fund_valuation
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update, delete on public.fundamentals, public.fund_valuation
  to app_backend;

create policy backend_all on public.fundamentals
  for all to app_backend using (true) with check (true);
create policy backend_all on public.fund_valuation
  for all to app_backend using (true) with check (true);
