-- Phases 3-5: price/action truth, periodized financials and forward data,
-- company graph, patent families and evidence-linked corporate events.

alter table public.price_bar
  add column if not exists listing_id bigint references public.security_listing(id),
  add column if not exists bar_type text not null default 'adjusted'
    check (bar_type in ('raw','adjusted')),
  add column if not exists quote_unit text,
  add column if not exists mic text,
  add column if not exists session_date date,
  add column if not exists source_timestamp timestamptz,
  add column if not exists access_state text not null default 'available'
    check (access_state in ('available','not_covered','paid_only','access_unverified',
      'quota_exhausted','temporarily_failed','disputed'));
create index if not exists price_bar_listing_session
  on public.price_bar(listing_id,session_date,source,bar_type);

create table public.exchange_session (
  mic text not null, session_date date not null, open_at timestamptz, close_at timestamptz,
  state text not null check(state in ('open','half_day','closed')), source text not null,
  confirmed boolean not null default false, evidence_url text, known_at timestamptz not null default now(),
  primary key(mic,session_date,source)
);
create table public.corporate_action (
  id bigint generated always as identity primary key,
  listing_id bigint references public.security_listing(id), symbol text not null,
  action_type text not null check(action_type in ('split','cash_dividend','stock_dividend',
    'symbol_change','merger','spin_off','delisting')),
  ex_date date not null, record_date date, payment_date date,
  ratio numeric, cash_amount numeric, currency text check(currency is null or currency ~ '^[A-Z]{3}$'),
  successor_listing_id bigint references public.security_listing(id),
  source text not null, source_url text not null, publisher_at timestamptz,
  known_at timestamptz not null default now(), evidence_id bigint references public.source_evidence(id),
  status text not null default 'confirmed' check(status in ('suggested','confirmed','rejected')),
  raw jsonb not null default '{}', unique(listing_id,action_type,ex_date,source,source_url)
);
create table public.price_return_series (
  id bigint generated always as identity primary key, symbol text not null,
  start_date date not null, end_date date not null, currency text not null,
  adjustment_basis text not null check(adjustment_basis in ('price_raw','price_adjusted','total_return')),
  source text not null, value numeric, coverage numeric not null check(coverage between 0 and 1),
  expected_sessions integer not null, observed_sessions integer not null,
  missing_actions text[] not null default '{}', known_at timestamptz not null,
  details jsonb not null default '{}', check(end_date>=start_date), unique(symbol,start_date,end_date,adjustment_basis,source)
);

create table public.financial_statement_fact (
  id bigint generated always as identity primary key, issuer_id bigint references public.issuer(id),
  ticker text, accession text, form text, filing_url text, concept text not null, label text,
  statement text not null check(statement in ('income','cash_flow','balance_sheet','shares','segment','note')),
  period_start date, period_end date not null, fiscal_period text, basis text not null,
  value numeric, unit text not null, currency text, dimensions jsonb not null default '{}',
  source text not null, source_url text not null, published_at timestamptz, known_at timestamptz not null,
  revision integer not null default 0, amends_fact_id bigint references public.financial_statement_fact(id),
  text_block text, evidence_id bigint references public.source_evidence(id),
  validation jsonb not null default '{}', published boolean not null default false,
  check(value is not null or text_block is not null), check(period_start is null or period_end>=period_start)
);
create index financial_fact_asof on public.financial_statement_fact(ticker,concept,period_end,known_at desc);

create table public.forward_observation (
  id bigint generated always as identity primary key, ticker text not null,
  kind text not null check(kind in ('company_guidance','owner_scenario','published_analyst_consensus','event_calendar_estimate')),
  provider text, metric text not null, basis text, fiscal_period text not null,
  period_end date, currency text, value numeric, low numeric, high numeric, analyst_count integer,
  vintage text, published_at timestamptz, known_at timestamptz not null,
  source_url text not null, evidence_id bigint references public.source_evidence(id),
  raw jsonb not null default '{}', check(value is not null or low is not null or high is not null)
);
create index forward_observation_asof on public.forward_observation(ticker,metric,fiscal_period,known_at desc);

create table public.peer_set (
  id bigint generated always as identity primary key, issuer_id bigint references public.issuer(id),
  ticker text not null, peer_ticker text not null, basis text not null,
  status text not null check(status in ('suggested','confirmed','rejected')),
  evidence_id bigint references public.source_evidence(id), source text not null,
  valid_from date, valid_to date, known_at timestamptz not null default now(), detail jsonb not null default '{}',
  unique(ticker,peer_ticker,basis,source)
);

create table public.company_graph_edge (
  id bigint generated always as identity primary key,
  subject_issuer_id bigint references public.issuer(id), subject_key text not null,
  object_issuer_id bigint references public.issuer(id), object_key text not null, object_name text not null,
  edge_type text not null check(edge_type in ('partner','acquirer','acquired','investor','investment',
    'product','technology','executive','customer','supplier','competitor','parent','subsidiary')),
  direction text not null check(direction in ('outgoing','incoming','symmetric')),
  status text not null check(status in ('suggested','confirmed','rejected','disputed')),
  valid_from date, valid_to date, evidence_tier text not null,
  source text not null, source_url text not null, evidence_id bigint references public.source_evidence(id),
  passage text, known_at timestamptz not null default now(), decided_at timestamptz,
  decision_reason text, detail jsonb not null default '{}',
  check(valid_to is null or valid_from is null or valid_to>=valid_from),
  unique(subject_key,object_key,edge_type,source,source_url)
);
create index company_graph_subject on public.company_graph_edge(subject_key,status,edge_type,known_at desc);
create table public.company_event (
  id bigint generated always as identity primary key, issuer_id bigint references public.issuer(id),
  ticker text not null, event_type text not null check(event_type in ('deal','financing','leadership',
    'guidance','relationship','product','patent')),
  event_state text not null check(event_state in ('announced','amended','proposed','approved','closed','terminated','reversed','unknown')),
  announced_at date, effective_at date, known_at timestamptz not null, source text not null,
  source_url text not null, evidence_id bigint references public.source_evidence(id),
  counterparty_key text, terms jsonb not null default '{}', status text not null default 'suggested'
    check(status in ('suggested','confirmed','rejected','disputed')),
  thesis_kpi text, detail jsonb not null default '{}', unique(ticker,event_type,source_url,event_state)
);
create table public.relationship_disclosure (
  id bigint generated always as identity primary key, ticker text not null,
  relationship text not null check(relationship in ('customer','supplier','procurement_award')),
  direction text not null check(direction in ('issuer_sells_to','issuer_buys_from','award_to_issuer','unknown')),
  counterparty_key text, counterparty_name text, anonymous_counterparty boolean not null default false,
  reported_share numeric check(reported_share between 0 and 1), share_basis text,
  fiscal_period text, valid_from date, valid_to date,
  evidence_id bigint references public.source_evidence(id), passage text not null,
  source_url text not null, source text not null,
  status text not null check(status in ('suggested','confirmed','rejected','disputed')),
  decision_reason text, known_at timestamptz not null default now(), decided_at timestamptz,
  check((anonymous_counterparty and counterparty_name is null) or not anonymous_counterparty),
  check(valid_to is null or valid_from is null or valid_to>=valid_from)
);
create index relationship_disclosure_concentration on public.relationship_disclosure
  (ticker,relationship,status,fiscal_period);
create table public.patent_family (
  id bigint generated always as identity primary key, family_key text not null unique,
  priority_date date, first_publication_date date, family_title text,
  cpc_codes text[] not null default '{}', jurisdictions text[] not null default '{}',
  citations_forward integer not null default 0, citations_backward integer not null default 0,
  legal_status text not null check(legal_status in ('application','granted','active','expired','unknown')),
  source text not null, source_url text not null, known_at timestamptz not null,
  raw jsonb not null default '{}'
);
create table public.patent_family_owner (
  family_id bigint not null references public.patent_family(id) on delete cascade,
  issuer_id bigint references public.issuer(id), ticker text not null, applicant text not null,
  ownership_confidence numeric not null check(ownership_confidence between 0 and 1),
  ownership_basis text not null check(ownership_basis in ('direct','dated_subsidiary_path','suggested')),
  valid_from date, valid_to date, evidence_id bigint references public.source_evidence(id),
  primary key(family_id,ticker,applicant)
);

create table public.issuer_ir_release (
  id bigint generated always as identity primary key, ticker text not null,
  release_type text not null check(release_type in ('guidance','consensus','segment_consensus','capital_return')),
  source_url text not null, content_hash text not null, document_version_id bigint references public.source_document_version(id),
  published_at timestamptz, known_at timestamptz not null, parser_version text,
  analyst_count integer, basis text, status text not null default 'available', metadata jsonb not null default '{}',
  unique(ticker,source_url,content_hash)
);

do $$ declare t text; begin foreach t in array array[
 'exchange_session','corporate_action','price_return_series','financial_statement_fact',
 'forward_observation','peer_set','company_graph_edge','company_event','relationship_disclosure','patent_family',
 'patent_family_owner','issuer_ir_release'] loop
 execute format('alter table public.%I enable row level security',t);
 execute format('revoke all on public.%I from anon,authenticated',t);
 execute format('grant select on public.%I to authenticated',t);
 execute format('grant select,insert,update,delete on public.%I to app_backend',t);
 execute format('create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))',t);
 execute format('create policy backend_all on public.%I for all to app_backend using (true) with check (true)',t);
end loop; end $$;
grant usage,select on all sequences in schema public to app_backend;

-- Free price/fundamental probes are opt-in until owner-key access has been tested.
insert into public.provider_access(provider,endpoint,state,credential_env,quota,quota_period,detail)
values ('alpha_vantage','daily_adjusted','access_unverified','ALPHA_VANTAGE_API_KEY',25,'day','owner-key endpoint probe'),
 ('alpha_vantage','commodity_fx','access_unverified','ALPHA_VANTAGE_API_KEY',25,'day','owner-key endpoint probe'),
 ('twelve_data','quote','access_unverified','TWELVE_DATA_API_KEY',null,null,'owner-key endpoint probe'),
 ('finnhub','quote','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe'),
 ('finnhub','peers','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe'),
 ('finnhub','earnings_surprises','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe'),
 ('alpha_vantage','financial_statements','access_unverified','ALPHA_VANTAGE_API_KEY',25,'day','owner-key endpoint probe'),
 ('fmp','sector_snapshot','access_unverified','FMP_API_KEY',250,'day','owner-key endpoint probe'),
 ('fmp','analyst_estimates','access_unverified','FMP_API_KEY',250,'day','owner-key endpoint probe'),
 ('diffbot','organization','access_unverified','DIFFBOT_API_KEY',10000,'month','owner-key endpoint probe'),
 ('people_data_labs','company_enrich','access_unverified','PDL_API_KEY',100,'month','owner-key endpoint probe'),
 ('epo_ops','patent_search','access_unverified','EPO_OPS_CLIENT_ID',null,'week','OAuth and OPS access probe')
on conflict(provider,endpoint) do nothing;
insert into public.provider_access(provider,endpoint,state,credential_env,quota,quota_period,detail)
values ('eodhd','symbol_search','access_unverified','EODHD_API_KEY',20,'day','owner-key endpoint probe'),
 ('finnhub','earnings_surprises','access_unverified','FINNHUB_API_KEY',null,null,'owner-key endpoint probe')
on conflict(provider,endpoint) do nothing;
insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values ('alpha_vantage','daily_adjusted','/query?function=TIME_SERIES_DAILY_ADJUSTED','OHLCV and adjusted close','fallback listing prices'),
 ('alpha_vantage','commodity_fx','/query?function={commodity-or-FX}','series-specific','issuer exposure context'),
 ('alpha_vantage','financial_statements','/query?function={INCOME_STATEMENT|BALANCE_SHEET|CASH_FLOW}','provider statement fields','field-level gap fill'),
 ('twelve_data','quote','/quote','quote currency','price cross-check'),
 ('finnhub','quote','/api/v1/quote','listing quote','price cross-check'),
 ('finnhub','peers','/api/v1/stock/peers','symbols','peer candidate discovery'),
 ('finnhub','earnings_surprises','/api/v1/stock/earnings','actual and estimate','event-level surprise'),
 ('fmp','sector_snapshot','/stable/sector-performance-snapshot','sector fields','US sector comparison'),
 ('fmp','analyst_estimates','/stable/analyst-estimates','estimate fields','forward consensus'),
 ('diffbot','organization','/v3/entity','organization fields','business graph suggestions'),
 ('people_data_labs','company_enrich','/v5/company/enrich','company fields','identity and headcount gap fill'),
 ('epo_ops','patent_search','/3.2/rest-services/published-data/search','bibliographic records','patent family analysis')
on conflict(provider,endpoint) do nothing;
insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values ('eodhd','symbol_search','/api/exchange-symbol-list/{exchange}','listing symbols','exchange-confirmed symbol matching'),
 ('finnhub','earnings_surprises','/api/v1/stock/earnings','event estimate and actual','earnings event surprise')
on conflict(provider,endpoint) do nothing;
