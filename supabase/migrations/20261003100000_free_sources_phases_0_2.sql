-- Free data sources phases 0-2: canonical identity, governed provenance, documents,
-- versioned macro/trade observations and explicit exposure mappings.

create table public.issuer (
  id bigint generated always as identity primary key,
  legal_name text not null,
  lei text, cik text, website text, country text check (country is null or country ~ '^[A-Z]{2}$'),
  valid_from date, valid_to date, created_at timestamptz not null default now()
);
create unique index issuer_lei_unique on public.issuer(lei) where lei is not null;
create unique index issuer_cik_unique on public.issuer(cik) where cik is not null;
create table public.security (
  id bigint generated always as identity primary key,
  issuer_id bigint references public.issuer(id),
  isin text, figi text, kind text not null,
  name text not null, currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  valid_from date, valid_to date
);
create unique index security_isin_unique on public.security(isin) where isin is not null;
create unique index security_figi_unique on public.security(figi) where figi is not null;
create table public.security_listing (
  id bigint generated always as identity primary key,
  security_id bigint not null references public.security(id), mic text not null,
  ticker text not null, currency text not null check (currency ~ '^[A-Z]{3}$'),
  status text not null default 'active' check (status in ('active','suspended','terminated','cancelled')),
  valid_from date, valid_to date, successor_listing_id bigint references public.security_listing(id),
  unique (security_id, mic, ticker, valid_from)
);
create table public.instrument_security_link (
  ticker text primary key references public.instrument(t212_ticker),
  security_id bigint references public.security(id), listing_id bigint references public.security_listing(id),
  status text not null check (status in ('confirmed','suggested','unresolved','rejected')),
  score numeric check (score between 0 and 1), evidence jsonb not null default '{}',
  decided_at timestamptz, decided_by uuid, updated_at timestamptz not null default now()
);
create table public.provider_identifier (
  id bigint generated always as identity primary key, provider text not null,
  identifier_type text not null, identifier text not null,
  issuer_id bigint references public.issuer(id), security_id bigint references public.security(id),
  listing_id bigint references public.security_listing(id), valid_from date, valid_to date,
  match_status text not null check (match_status in ('confirmed','suggested','rejected')),
  score numeric check (score between 0 and 1), evidence jsonb not null default '{}',
  unique (provider, identifier_type, identifier, valid_from)
);
create table public.provider_access (
  provider text not null, endpoint text not null, account_tier text not null default 'free',
  state text not null check (state in ('available','not_covered','paid_only','access_unverified','quota_exhausted','temporarily_failed','disputed')),
  credential_env text, quota numeric, quota_period text, reset_timezone text,
  request_weight numeric not null default 1, tested_at timestamptz, retry_at timestamptz,
  status_code integer, detail text, sample_fixture text, updated_at timestamptz not null default now(),
  primary key (provider, endpoint)
);
create table public.provider_quota_usage (
  provider text not null, endpoint text not null, window_start timestamptz not null,
  window_end timestamptz not null, credits numeric not null check (credits >= 0),
  requests integer not null check (requests >= 0), reserved numeric not null default 0,
  primary key (provider, endpoint, window_start)
);
create table public.source_observation (
  id bigint generated always as identity primary key, domain text not null, metric text not null,
  issuer_id bigint references public.issuer(id), security_id bigint references public.security(id),
  listing_id bigint references public.security_listing(id), geography text,
  period_start date not null, period_end date not null, fiscal_period text,
  value numeric, text_value text, unit text not null, scale numeric not null default 1, currency text,
  source text not null, source_url text not null, publisher_at timestamptz,
  retrieved_at timestamptz not null default now(), known_at timestamptz not null,
  valid_from timestamptz, valid_to timestamptz, vintage text, revision integer not null default 0,
  transformation_version text, source_priority integer, access_state text not null,
  raw jsonb not null default '{}', normalized jsonb not null default '{}', validation jsonb not null default '{}',
  confidence numeric check (confidence between 0 and 1), published boolean not null default false,
  check (value is not null or text_value is not null), check (period_end >= period_start)
);
create unique index source_observation_identity on public.source_observation
  (domain, metric, coalesce(issuer_id,0), coalesce(security_id,0), coalesce(listing_id,0),
   coalesce(geography,''), period_end, source, coalesce(vintage,''), revision);
create index source_observation_asof on public.source_observation(metric, known_at, period_end desc);
create table public.source_conflict (
  id bigint generated always as identity primary key, metric text not null,
  preferred_observation_id bigint references public.source_observation(id),
  alternative_observation_id bigint not null references public.source_observation(id),
  tolerance numeric, difference numeric, status text not null default 'open'
    check (status in ('open','accepted','rejected','resolved')),
  resolution text, decided_at timestamptz, decided_by uuid, created_at timestamptz not null default now()
);
create unique index source_conflict_pair on public.source_conflict
  (preferred_observation_id,alternative_observation_id);
create table public.coverage_status (
  provider text not null, dataset text not null, entity_key text not null,
  state text not null check (state in ('available','not_covered','paid_only','access_unverified','quota_exhausted','temporarily_failed','disputed','vintage_unavailable')),
  expected_periods integer, available_periods integer, earliest_period date, latest_period date,
  last_good_observation_id bigint references public.source_observation(id),
  checked_at timestamptz not null default now(), reason text,
  primary key(provider,dataset,entity_key)
);
create table public.source_document (
  id bigint generated always as identity primary key, regulator text not null,
  external_id text not null, stable_url text not null, document_type text,
  unique(regulator, external_id)
);
create table public.source_document_version (
  id bigint generated always as identity primary key, document_id bigint not null references public.source_document(id),
  content_hash text not null, filed_at timestamptz, accepted_at timestamptz, published_at timestamptz,
  retrieved_at timestamptz not null default now(), known_at timestamptz not null,
  amends_version_id bigint references public.source_document_version(id),
  superseded_by_id bigint references public.source_document_version(id), raw_location text,
  unique(document_id, content_hash)
);
create table public.source_evidence (
  id bigint generated always as identity primary key,
  document_version_id bigint references public.source_document_version(id),
  observation_id bigint references public.source_observation(id), page text, table_name text,
  span_start integer, span_end integer, quote text, created_at timestamptz not null default now(),
  check (document_version_id is not null or observation_id is not null)
);
create table public.macro_observation (
  id bigint generated always as identity primary key, series_id text not null, source text not null
    check (source in ('fred','alfred','ecb','eurostat','cnb','world_bank','oecd','bis','un_comtrade')),
  geography text not null, period date not null, value numeric, unit text not null,
  unit_multiplier numeric not null default 1, frequency text not null,
  seasonal_adjustment text, release_at timestamptz, known_at timestamptz not null,
  vintage text not null, revision integer not null default 0, transformation text,
  source_url text not null, metadata jsonb not null default '{}', fetched_at timestamptz not null default now(),
  unique(series_id, source, geography, period, vintage, revision)
);
create index macro_observation_asof on public.macro_observation(series_id, geography, known_at, period desc);
create table public.trade_observation (
  id bigint generated always as identity primary key, reporter text not null, partner text not null,
  flow text not null check(flow in ('import','export')), hs_revision text not null, hs_code text not null,
  period date not null, trade_value numeric, quantity numeric, quantity_unit text,
  release_at timestamptz, known_at timestamptz not null, source_url text not null,
  raw jsonb not null default '{}', unique(reporter,partner,flow,hs_revision,hs_code,period,known_at)
);
create table public.exposure_mapping (
  id bigint generated always as identity primary key, issuer_id bigint not null references public.issuer(id),
  segment text not null, geography text, hs_revision text, hs_code text,
  basis text not null check(basis in ('reported','estimated','user_assigned')),
  weight numeric not null check(weight >= 0 and weight <= 1), evidence_id bigint references public.source_evidence(id),
  valid_from date not null, valid_to date, created_at timestamptz not null default now(), created_by uuid,
  check(geography is not null or hs_code is not null)
);

-- Every table is private to the owner in PostgREST and writable only by app_backend.
do $$ declare t text; begin foreach t in array array[
'issuer','security','security_listing','instrument_security_link','provider_identifier','provider_access',
'provider_quota_usage','source_observation','source_conflict','coverage_status','source_document',
'source_document_version','source_evidence','macro_observation','trade_observation','exposure_mapping'] loop
 execute format('alter table public.%I enable row level security', t);
 execute format('revoke all on public.%I from anon, authenticated', t);
 execute format('grant select on public.%I to authenticated', t);
 execute format('grant select, insert, update, delete on public.%I to app_backend', t);
 execute format('create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))', t);
 execute format('create policy backend_all on public.%I for all to app_backend using (true) with check (true)', t);
end loop; end $$;
grant usage, select on all sequences in schema public to app_backend;

-- Preserve old tables while admitting the new macro providers.
alter table public.macro_series drop constraint macro_series_source_check;
alter table public.macro_series add constraint macro_series_source_check
 check(source in ('fred','ecb','eurostat','cnb','world_bank','oecd','bis','un_comtrade'));
