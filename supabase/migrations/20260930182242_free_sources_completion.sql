-- Complete the operational Phase 0-2 contract: durable endpoint manifests and attempts,
-- atomic cross-worker quota reservation, identity decisions/lifecycle, and macro metadata.

alter table public.provider_access
  add column if not exists hard_blocked boolean not null default false,
  add column if not exists configuration_hash text,
  add column if not exists last_success_at timestamptz;

alter table public.provider_quota_usage drop constraint provider_quota_usage_pkey;
alter table public.provider_quota_usage
  add column window_kind text not null default 'day'
    check (window_kind in ('minute','day','month'));
alter table public.provider_quota_usage
  add primary key (provider, endpoint, window_kind, window_start);

create table public.provider_endpoint_manifest (
  provider text not null,
  endpoint text not null,
  method text not null default 'GET' check (method in ('GET','POST')),
  path_template text not null,
  parameters jsonb not null default '{}',
  pagination jsonb not null default '{}',
  timezone text,
  response_unit text,
  update_cadence interval,
  request_weight numeric not null default 1 check (request_weight > 0),
  retention interval,
  sample_fixture text,
  production_consumer text not null,
  primary key (provider, endpoint),
  foreign key (provider, endpoint) references public.provider_access(provider, endpoint)
    on update cascade on delete cascade
);

-- Audited endpoint gates.  Account-dependent endpoints stay unverified and therefore
-- hard-blocked until an owner-key probe records an available result.
insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked)
values
 ('eurostat','dissemination','available',null,null,null,1,'existing public collector',false),
 ('fred','observations','access_unverified','FRED_API_KEY',null,null,1,'API key required',false),
 ('fred','vintages','access_unverified','FRED_API_KEY',null,null,1,'API key required',false),
 ('cnb','series','available',null,null,null,1,'existing public collector',false),
 ('alpha_vantage','company_overview','available','ALPHA_VANTAGE_API_KEY',25,'day',1,'existing collector',false),
 ('alpha_vantage','news_sentiment','access_unverified','ALPHA_VANTAGE_API_KEY',25,'day',1,'owner-key probe required',false),
 ('eodhd','eod','access_unverified','EODHD_API_KEY',20,'day',1,'owner-key probe required',false),
 ('world_bank','indicators','available',null,null,null,1,'keyless public API',false),
 ('oecd','sdmx','available',null,null,null,1,'keyless public SDMX',false),
 ('fmp','profile','access_unverified','FMP_API_KEY',250,'day',1,'owner-key probe required',false),
 ('fmp','analyst_estimates','access_unverified','FMP_API_KEY',250,'day',1,'owner-key probe required',false),
 ('diffbot','extract','access_unverified','DIFFBOT_API_KEY',10000,'month',1,'owner-key probe required',false),
 ('people_data_labs','company_enrich','access_unverified','PDL_API_KEY',100,'month',1,'company-only probe required',false),
 ('epo_ops','published_data','access_unverified','EPO_OPS_CLIENT_ID',null,'week',1,'OAuth credentials required',false),
 ('finnhub','earnings_calendar','available','FINNHUB_API_KEY',null,null,1,'existing collector',false),
 ('finnhub','ownership','access_unverified','FINNHUB_API_KEY',null,null,1,'owner-key probe required',false),
 ('twelve_data','time_series','access_unverified','TWELVE_DATA_API_KEY',null,null,1,'owner-key probe required',false),
 ('twelve_data','corporate_actions','access_unverified','TWELVE_DATA_API_KEY',null,null,1,'owner-key probe required',false),
 ('bis','sdmx','available',null,null,null,1,'keyless public SDMX',false),
 ('ecb','sdmx','available',null,null,null,1,'keyless public SDMX',false),
 ('un_comtrade','trade','access_unverified','UN_COMTRADE_API_KEY',500,'day',1,'registered-key probe required',false)
on conflict (provider,endpoint) do nothing;

insert into public.provider_endpoint_manifest
  (provider,endpoint,path_template,response_unit,request_weight,production_consumer)
values
 ('eurostat','dissemination','/api/dissemination/statistics/1.0/data/{dataset}','dataset unit',1,'macro history and first-seen releases'),
 ('fred','observations','/fred/series/observations','series-specific',1,'US macro context'),
 ('fred','vintages','/fred/series/observations?output_type=4','series-specific vintage',1,'as-of US macro history'),
 ('cnb','series','/statistics/interest-rates','percent',1,'CZ monetary context'),
 ('alpha_vantage','company_overview','/query?function=OVERVIEW','provider fields',1,'valuation fallback'),
 ('alpha_vantage','news_sentiment','/query?function=NEWS_SENTIMENT','articles',1,'research candidates'),
 ('eodhd','eod','/api/eod/{symbol}','listing currency',1,'price fallback'),
 ('world_bank','indicators','/v2/country/{country}/indicator/{indicator}','indicator metadata',1,'country context'),
 ('oecd','sdmx','/public/rest/v1/data/{flow}/{key}','series metadata',1,'country context'),
 ('fmp','profile','/stable/profile','provider fields',1,'issuer profile'),
 ('fmp','analyst_estimates','/stable/analyst-estimates','estimate metadata',1,'estimate coverage gap'),
 ('diffbot','extract','/v3/article','document fields',1,'research suggestions'),
 ('people_data_labs','company_enrich','/v5/company/enrich','company fields',1,'identity resolution'),
 ('epo_ops','published_data','/3.2/rest-services/published-data/{id}/biblio','bibliographic fields',1,'patent research'),
 ('finnhub','earnings_calendar','/api/v1/calendar/earnings','event fields',1,'earnings calendar'),
 ('finnhub','ownership','/api/v1/stock/ownership','ownership fields',1,'ownership coverage gap'),
 ('twelve_data','time_series','/time_series','quote currency',1,'price cross-check'),
 ('twelve_data','corporate_actions','/dividends|/splits','action fields',1,'corporate-action gap'),
 ('bis','sdmx','/api/v2/data/{flow}/{key}','series metadata',1,'financial-cycle context'),
 ('ecb','sdmx','/service/data/{flow}/{key}','series metadata',1,'rates and credit context'),
 ('un_comtrade','trade','/data/v1/get/C/A/HS','USD and quantity',1,'trade exposure')
on conflict (provider,endpoint) do nothing;

create table public.provider_fetch_audit (
  id bigint generated always as identity primary key,
  provider text not null,
  endpoint text not null,
  attempted_at timestamptz not null default now(),
  state text not null check (state in ('available','not_covered','paid_only',
    'access_unverified','quota_exhausted','temporarily_failed','disputed')),
  status_code integer,
  request_id text,
  elapsed_ms integer check (elapsed_ms is null or elapsed_ms >= 0),
  response_bytes bigint check (response_bytes is null or response_bytes >= 0),
  credits numeric not null default 0 check (credits >= 0),
  retry_at timestamptz,
  error_redacted text,
  foreign key (provider, endpoint) references public.provider_access(provider, endpoint)
);
create index provider_fetch_audit_recent
  on public.provider_fetch_audit(provider, endpoint, attempted_at desc);

create or replace function public.record_provider_attempt(
  p_provider text, p_endpoint text, p_state text, p_detail text, p_retry_at timestamptz,
  p_status_code integer, p_request_id text, p_elapsed_ms integer,
  p_response_bytes bigint, p_credits numeric
) returns void language plpgsql security invoker set search_path = '' as $$
begin
  insert into public.provider_fetch_audit
    (provider,endpoint,state,status_code,request_id,elapsed_ms,response_bytes,credits,retry_at,error_redacted)
  values (p_provider,p_endpoint,p_state,p_status_code,p_request_id,p_elapsed_ms,
          p_response_bytes,p_credits,p_retry_at,p_detail);
  update public.provider_access set state=p_state, status_code=p_status_code, detail=p_detail,
    retry_at=p_retry_at, tested_at=now(), updated_at=now(),
    hard_blocked=case when p_state='paid_only'
                           or (p_state='access_unverified' and p_status_code in (401,403)) then true
                      when p_state='available' then false else hard_blocked end,
    last_success_at=case when p_state='available' then now() else last_success_at end
  where provider=p_provider and endpoint=p_endpoint;
end $$;

create or replace function public.reserve_provider_credits(
  p_provider text, p_endpoint text, p_cost numeric, p_per_minute numeric, p_daily numeric,
  p_monthly numeric, p_reserve numeric, p_now timestamptz
) returns boolean language plpgsql security invoker set search_path = '' as $$
declare n_start timestamptz := date_trunc('minute', p_now);
declare d_start timestamptz := date_trunc('day', p_now);
declare m_start timestamptz := date_trunc('month', p_now);
declare n_used numeric; declare d_used numeric; declare m_used numeric; declare blocked boolean;
begin
  if p_cost <= 0 or p_reserve < 0 then raise exception 'invalid provider credit request'; end if;
  perform pg_advisory_xact_lock(hashtext(p_provider), hashtext(p_endpoint));
  select hard_blocked into blocked from public.provider_access
   where provider=p_provider and endpoint=p_endpoint for update;
  if blocked is null or blocked then return false; end if;
  select coalesce(max(credits),0) into n_used from public.provider_quota_usage
   where provider=p_provider and endpoint=p_endpoint and window_kind='minute'
     and window_start=n_start;
  select coalesce(sum(credits),0) into d_used from public.provider_quota_usage
   where provider=p_provider and endpoint=p_endpoint and window_kind='day'
     and window_start=d_start;
  select coalesce(sum(credits),0) into m_used from public.provider_quota_usage
   where provider=p_provider and endpoint=p_endpoint and window_kind='month'
     and window_start=m_start;
  if (p_per_minute is not null and n_used+p_cost > p_per_minute)
     or (p_daily is not null and d_used+p_cost > p_daily-p_reserve)
     or (p_monthly is not null and m_used+p_cost > p_monthly-p_reserve) then return false; end if;
  insert into public.provider_quota_usage(provider,endpoint,window_kind,window_start,window_end,credits,requests)
    values (p_provider,p_endpoint,'minute',n_start,n_start+interval '1 minute',p_cost,1)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1;
  insert into public.provider_quota_usage(provider,endpoint,window_kind,window_start,window_end,credits,requests)
    values (p_provider,p_endpoint,'day',d_start,d_start+interval '1 day',p_cost,1)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1;
  insert into public.provider_quota_usage(provider,endpoint,window_kind,window_start,window_end,credits,requests)
    values (p_provider,p_endpoint,'month',m_start,m_start+interval '1 month',p_cost,1)
    on conflict (provider,endpoint,window_kind,window_start) do update
      set credits=provider_quota_usage.credits+excluded.credits,
          requests=provider_quota_usage.requests+1;
  return true;
end $$;

create table public.identity_match_decision (
  id bigint generated always as identity primary key,
  provider_identifier_id bigint references public.provider_identifier(id),
  instrument_ticker text references public.instrument(t212_ticker),
  candidate_issuer_id bigint references public.issuer(id),
  candidate_security_id bigint references public.security(id),
  candidate_listing_id bigint references public.security_listing(id),
  decision text not null check (decision in ('confirmed','suggested','rejected','unresolved')),
  score numeric check (score between 0 and 1),
  reasons jsonb not null default '[]',
  decided_at timestamptz not null default now(), decided_by uuid,
  check (provider_identifier_id is not null or instrument_ticker is not null)
);
create table public.issuer_alias (
  id bigint generated always as identity primary key, issuer_id bigint not null references public.issuer(id),
  name text not null, kind text not null check(kind in ('legal','former','trading','translated')),
  valid_from date, valid_to date, source_document_version_id bigint references public.source_document_version(id),
  unique(issuer_id,name,valid_from)
);
create table public.issuer_relationship (
  id bigint generated always as identity primary key,
  predecessor_id bigint not null references public.issuer(id),
  successor_id bigint not null references public.issuer(id),
  relationship text not null check(relationship in ('renamed_to','merged_into','spun_off')),
  valid_from date not null, evidence_id bigint references public.source_evidence(id),
  check(predecessor_id <> successor_id), unique(predecessor_id,successor_id,relationship,valid_from)
);

create table public.macro_series_metadata (
  source text not null, series_id text not null, label text not null,
  definition text not null, frequency text not null, unit text not null,
  unit_multiplier numeric not null default 1, seasonal_adjustment text,
  transformation text, geography_definition text, release_calendar text,
  code_lists jsonb not null default '{}', consumer text not null,
  primary key(source,series_id)
);
create table public.macro_payload_archive (
  id bigint generated always as identity primary key, source text not null,
  dataset text not null, retrieved_at timestamptz not null default now(),
  content_hash text not null, known_at timestamptz not null,
  source_url text not null, storage_location text, payload jsonb,
  unique(source,dataset,content_hash)
);

do $$ declare t text; begin foreach t in array array[
  'provider_endpoint_manifest','provider_fetch_audit','identity_match_decision','issuer_alias',
  'issuer_relationship','macro_series_metadata','macro_payload_archive'
] loop
 execute format('alter table public.%I enable row level security', t);
 execute format('revoke all on public.%I from anon, authenticated', t);
 execute format('grant select on public.%I to authenticated', t);
 execute format('grant select, insert, update, delete on public.%I to app_backend', t);
 execute format('create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))', t);
 execute format('create policy backend_all on public.%I for all to app_backend using (true) with check (true)', t);
end loop; end $$;
grant execute on function public.record_provider_attempt(text,text,text,text,timestamptz,integer,text,integer,bigint,numeric) to app_backend;
grant execute on function public.reserve_provider_credits(text,text,numeric,numeric,numeric,numeric,numeric,timestamptz) to app_backend;
grant usage, select on all sequences in schema public to app_backend;
