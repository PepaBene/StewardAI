-- Structured Section 16 Forms 3, 4 and 5 for instruments held in the portfolio.
create table public.insider_filing (
  accession text primary key check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  ticker text not null,
  issuer_cik text not null check (issuer_cik ~ '^[0-9]{10}$'),
  form text not null check (form in ('3', '3/A', '4', '4/A', '5', '5/A')),
  filed date not null,
  period_of_report date,
  issuer_name text,
  source_url text not null,
  loaded_at timestamptz not null default now()
);
create index insider_filing_ticker_date on public.insider_filing (ticker, filed desc);

create table public.insider_transaction (
  accession text not null references public.insider_filing (accession) on delete cascade,
  row_index integer not null check (row_index >= 0),
  reporting_owner_cik text not null check (reporting_owner_cik ~ '^[0-9]{10}$'),
  owner_name text,
  officer_title text,
  is_director boolean not null default false,
  is_officer boolean not null default false,
  is_ten_percent_owner boolean not null default false,
  security_title text,
  transaction_date date,
  transaction_code text not null,
  shares numeric,
  price_per_share numeric,
  acquired_disposed text check (acquired_disposed is null or acquired_disposed in ('A', 'D')),
  shares_owned_after numeric,
  direct_indirect text,
  derivative boolean not null default false,
  primary key (accession, row_index)
);
create index insider_transaction_accession on public.insider_transaction (accession, transaction_code);

alter table public.insider_filing enable row level security;
alter table public.insider_transaction enable row level security;
revoke all on public.insider_filing, public.insider_transaction from anon, authenticated;
grant select on public.insider_filing, public.insider_transaction to authenticated;
create policy owner_read on public.insider_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.insider_transaction
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.insider_filing, public.insider_transaction to app_backend;
create policy backend_all on public.insider_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.insider_transaction
  for all to app_backend using (true) with check (true);
