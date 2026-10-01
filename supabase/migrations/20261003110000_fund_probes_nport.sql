-- D6.2 opt-in fund/ownership provider probes and D6.3 SEC N-PORT fund holdings.
-- N-PORT rows are FUND POSITIONS (what a registered fund reports owning, up to ~60 days after
-- quarter end). They are not beneficial ownership and are never added to 13F or 13D/G figures.

-- Endpoint gates: unverified (and hard-blocked on a paid/403 denial) until an owner-key probe passes.
insert into public.provider_access(provider,endpoint,state,credential_env,quota,quota_period,detail)
values
 ('finnhub','etf_profile','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe'),
 ('finnhub','etf_holdings','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe'),
 ('finnhub','index_constituents','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe'),
 ('finnhub','institutional_ownership','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe'),
 ('fmp','etf_info','access_unverified','FMP_API_KEY',250,'day','owner-key endpoint probe'),
 ('fmp','etf_holdings','access_unverified','FMP_API_KEY',250,'day','owner-key endpoint probe'),
 ('fmp','institutional_ownership','access_unverified','FMP_API_KEY',250,'day','owner-key endpoint probe')
on conflict(provider,endpoint) do nothing;

insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values
 ('finnhub','etf_profile','/api/v1/etf/profile','profile fields','fund coverage ledger (opt-in probe)'),
 ('finnhub','etf_holdings','/api/v1/etf/holdings','percent of fund','fund coverage ledger (opt-in probe)'),
 ('finnhub','index_constituents','/api/v1/index/constituents','symbols','fund coverage ledger (opt-in probe)'),
 ('finnhub','institutional_ownership','/api/v1/institutional/ownership','shares','ownership cross-check (opt-in probe)'),
 ('fmp','etf_info','/stable/etf/info','profile fields','fund coverage ledger (opt-in probe)'),
 ('fmp','etf_holdings','/stable/etf/holdings','percent of fund','fund coverage ledger (opt-in probe)'),
 ('fmp','institutional_ownership','/stable/institutional-ownership/extract-analytics/holder','shares','ownership cross-check (opt-in probe)')
on conflict(provider,endpoint) do nothing;

-- Typed records from a successful free probe response. Provider data only: never promoted over
-- an issuer/regulator source and never combined with 13F or N-PORT.
create table public.fund_probe_record (
  id bigint generated always as identity primary key,
  provider text not null,
  endpoint text not null,
  record_kind text not null check (record_kind in
    ('etf_profile','etf_holding','index_constituent','institutional_holder')),
  subject_symbol text not null,
  item_key text not null,
  item_name text,
  item_symbol text,
  isin text,
  cusip text,
  as_of date,
  weight_pct numeric,
  shares numeric,
  value numeric,
  currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  attributes jsonb not null default '{}',
  source_url text not null,
  payload_hash text not null,
  retrieved_at timestamptz not null default now(),
  foreign key (provider, endpoint) references public.provider_access(provider, endpoint),
  unique (provider, endpoint, subject_symbol, item_key, payload_hash)
);
create index fund_probe_record_subject on public.fund_probe_record(subject_symbol, record_kind, as_of desc);
create index fund_probe_record_endpoint on public.fund_probe_record(provider, endpoint);

-- One row per NPORT-P / NPORT-P/A filing (one fund series per filing).
create table public.nport_filing (
  accession text primary key check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  form text not null check (form in ('NPORT-P','NPORT-P/A')),
  registrant_cik text not null check (registrant_cik ~ '^[0-9]{10}$'),
  registrant_name text,
  registrant_lei text,
  series_id text not null check (series_id ~ '^S[0-9]{9}$'),
  series_name text,
  series_lei text,
  class_ids text[] not null default '{}',
  fiscal_year_end date,
  report_period_end date not null,
  filed date not null,
  is_final boolean not null default false,
  total_assets numeric,
  total_liabilities numeric,
  net_assets numeric,
  risk_metrics jsonb not null default '{}',
  class_returns jsonb not null default '[]',
  flows jsonb not null default '{}',
  holdings_count integer not null default 0 check (holdings_count >= 0),
  -- Fund identity and report dates must reconcile before the holdings are readable.
  reconcile_status text not null check (reconcile_status in ('confirmed','unmatched','date_mismatch')),
  reconcile_detail text,
  matched_ticker text references public.instrument(t212_ticker),
  fund_isin text,
  source_url text not null,
  document_version_id bigint references public.source_document_version(id),
  loaded_at timestamptz not null default now()
);
create index nport_filing_series on public.nport_filing(series_id, report_period_end desc, filed desc);
create index nport_filing_ticker on public.nport_filing(matched_ticker);
create index nport_filing_doc on public.nport_filing(document_version_id);

create table public.nport_holding (
  accession text not null references public.nport_filing(accession) on delete cascade,
  row_index integer not null check (row_index >= 0),
  issuer_name text,
  issuer_lei text,
  title text,
  cusip text,
  isin text,
  other_id text,
  balance numeric,
  units text,
  currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  value_usd numeric,
  pct_value numeric,
  payoff_profile text,
  asset_cat text,
  issuer_cat text,
  country text,
  fair_value_level text,
  is_restricted boolean,
  is_derivative boolean not null default false,
  derivative_cat text,
  -- Canonical issuer mapping: set only on an identifier match (ISIN, LEI or OpenFIGI CUSIP),
  -- never on a name; unmatched rows keep NULL and the filing's own identifiers.
  issuer_id bigint references public.issuer(id),
  security_id bigint references public.security(id),
  held_ticker text references public.instrument(t212_ticker),
  map_basis text check (map_basis is null or map_basis in ('isin','lei','cusip_figi','cusip_isin')),
  primary key (accession, row_index)
);
create index nport_holding_ticker on public.nport_holding(held_ticker) where held_ticker is not null;
create index nport_holding_issuer on public.nport_holding(issuer_id) where issuer_id is not null;
create index nport_holding_security on public.nport_holding(security_id) where security_id is not null;
create index nport_holding_cusip on public.nport_holding(cusip) where cusip is not null;

-- Owner-confirmed or SEC-suggested mapping from a held fund to its N-PORT series.
create table public.nport_fund_map (
  ticker text primary key references public.instrument(t212_ticker),
  registrant_cik text not null check (registrant_cik ~ '^[0-9]{10}$'),
  series_id text not null check (series_id ~ '^S[0-9]{9}$'),
  class_id text,
  evidence text not null,
  status text not null default 'suggested' check (status in ('confirmed','suggested','rejected')),
  updated_at timestamptz not null default now()
);

do $$ declare t text; begin foreach t in array array[
  'fund_probe_record','nport_filing','nport_holding','nport_fund_map'] loop
  execute format('alter table public.%I enable row level security', t);
  execute format('revoke all on public.%I from anon, authenticated', t);
  execute format('grant select on public.%I to authenticated', t);
  execute format('grant select, insert, update, delete on public.%I to app_backend', t);
  execute format('create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))', t);
  execute format('create policy backend_all on public.%I for all to app_backend using (true) with check (true)', t);
end loop; end $$;
grant usage, select on all sequences in schema public to app_backend;

-- Admit N-PORT evidence links. The allowed-table list is rebuilt from the live constraint plus
-- the new names so parallel migrations that also extend it are not clobbered by this one.
do $$
declare existing text[]; wanted text[] := array['nport_filing','nport_holding'];
begin
  select array(select m[1] from regexp_matches(pg_get_constraintdef(oid), '''([a-z0-9_]+)''', 'g') as m)
    into existing
    from pg_constraint
   where conrelid = 'public.source_record_evidence'::regclass
     and conname = 'source_record_evidence_domain_table_check';
  alter table public.source_record_evidence drop constraint source_record_evidence_domain_table_check;
  execute format(
    'alter table public.source_record_evidence add constraint source_record_evidence_domain_table_check check (domain_table in (%s))',
    (select string_agg(quote_literal(t), ',' order by t)
       from (select distinct unnest(coalesce(existing, '{}') || wanted) as t) u));
end $$;
