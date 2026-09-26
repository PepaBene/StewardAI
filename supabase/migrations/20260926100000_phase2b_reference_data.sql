-- Phase 2b: identity, reference data and market history.
--
--   instrument            + share class FIGI, home market, issuer LEI, Stooq symbol, Wikidata
--                         item, and when (or why not) identity was resolved.
--   instrument_identifier every OpenFIGI listing of a holding's share class.
--   listing               ESMA FIRDS: one row per ISIN and trading venue (MIC).
--   listing_liquidity     ESMA FITRS: liquidity, turnover and the most relevant market per ISIN.
--   legal_entity          GLEIF Level 1 records (issuers, parents, children).
--   entity_relationship   group structure (GLEIF Level 2), Wikidata links and filing mentions,
--                         per holding: confirmed, suggested (Wikidata or a filing alone) or
--                         rejected by the owner (never suggested again).
--   entity_alias          names that link news to a holding: legal and other names,
--                         subsidiaries, Wikidata aliases, listing tickers.
--   price_bar             daily OHLCV per symbol (Stooq).
--   holiday               public holidays (Nager.Date) and exchange closures (config).
--   fx_rate               + provider: which service delivered an ECB rate (the Data Portal or
--                         Frankfurter, which republishes it).
--   source                + type 'reference' for lifecycle events raised from reference data.
--
-- Same access model as every other table: RLS on, owner-only SELECT for the frontend,
-- `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

alter table public.instrument
  add column share_class_figi     text,
  add column security_type        text,
  add column home_mic             text,
  add column home_country         text check (home_country ~ '^[A-Z]{2}$'),
  add column issuer_lei           text check (issuer_lei ~ '^[A-Z0-9]{20}$'),
  add column price_symbol         text,
  add column wikidata_qid         text check (wikidata_qid ~ '^Q[0-9]+$'),
  add column wikidata             jsonb,
  add column identity_resolved_at timestamptz,
  add column identity_error       text;

comment on column public.instrument.identity_error is
  'Why identity could not be (fully) resolved; listed on the Activity page, never skipped silently.';

-- ---------------------------------------------------------------------------
-- Identifiers and listings
-- ---------------------------------------------------------------------------
create table public.instrument_identifier (
  ticker               text        not null references public.instrument (t212_ticker),
  figi                 text        not null,
  composite_figi       text,
  share_class_figi     text,
  listing_ticker       text,
  exch_code            text,
  name                 text,
  security_type        text,
  security_type2       text,
  market_sector        text,
  security_description text,
  cusip                text,
  source               text        not null default 'openfigi',
  raw                  jsonb       not null,
  fetched_at           timestamptz not null default now(),
  primary key (ticker, figi)
);

create table public.listing (
  isin           text        not null,
  mic            text        not null,
  full_name      text,
  short_name     text,
  cfi            text,
  currency       text,
  issuer_lei     text,
  admitted_at    timestamptz,
  first_trade_at timestamptz,
  terminated_at  timestamptz,
  status         text,
  source         text        not null default 'firds',
  raw            jsonb       not null,
  fetched_at     timestamptz not null default now(),
  primary key (isin, mic)
);

create table public.listing_liquidity (
  isin                 text        primary key,
  liquid               boolean,
  avg_daily_turnover   numeric,
  turnover_currency    text,
  avg_daily_trades     numeric,
  most_relevant_market text,
  period_from          date,
  period_to            date,
  raw                  jsonb       not null,
  fetched_at           timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Legal entities and structure
-- ---------------------------------------------------------------------------
create table public.legal_entity (
  lei                 text        primary key check (lei ~ '^[A-Z0-9]{20}$'),
  legal_name          text        not null,
  other_names         text[]      not null default '{}',
  jurisdiction        text,
  legal_form          text,
  category            text,
  hq_address          jsonb,
  legal_address       jsonb,
  hq_country          text,
  status              text,
  successor_lei       text,
  registration_status text,
  next_renewal        timestamptz,
  raw                 jsonb       not null,
  fetched_at          timestamptz not null default now()
);

create table public.entity_relationship (
  id               bigint generated always as identity primary key,
  -- The holding this relationship belongs to (its issuer is the subject).
  ticker           text        not null,
  subject          text        not null,
  subject_name     text,
  -- LEI, 'wd:<QID>' or a normalised name: what the relationship points at.
  object_key       text        not null,
  object_name      text        not null,
  object_lei       text,
  type             text        not null check (type in ('parent', 'ultimate_parent', 'subsidiary',
                                 'owner', 'competitor', 'supplier', 'customer')),
  source           text        not null check (source in ('gleif', 'wikidata', 'edgar', 'owner')),
  -- Every source that has reported it (a second source corroborates the first).
  sources          text[]      not null default '{}',
  source_ref       text,
  as_of            date,
  status           text        not null check (status in ('confirmed', 'suggested', 'rejected')),
  exception_reason text,
  validation_level text,
  detail           jsonb,
  decided_at       timestamptz,
  decided_by       uuid,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (ticker, type, object_key)
);

comment on table public.entity_relationship is
  'GLEIF parents are accounting-consolidation parents, not shareholders. Wikidata-only and '
  'filing-only relationships stay suggested until the owner confirms them.';

create trigger entity_relationship_set_updated_at
  before update on public.entity_relationship
  for each row execute function private.set_updated_at();

create index entity_relationship_ticker on public.entity_relationship (ticker, status);

-- A holding's GLEIF reporting exceptions (why no parent is reported).
create table public.entity_reporting_exception (
  lei        text        not null,
  category   text        not null,
  reason     text        not null,
  fetched_at timestamptz not null default now(),
  primary key (lei, category)
);

create table public.entity_alias (
  ticker     text        not null,
  alias      text        not null,
  kind       text        not null check (kind in ('legal_name', 'other_name', 'subsidiary',
                           'alias', 'listing_ticker')),
  source     text        not null,
  created_at timestamptz not null default now(),
  primary key (ticker, alias)
);

-- ---------------------------------------------------------------------------
-- Market history and calendars
-- ---------------------------------------------------------------------------
create table public.price_bar (
  symbol     text        not null,
  date       date        not null,
  open       numeric,
  high       numeric,
  low        numeric,
  close      numeric     not null check (close > 0),
  volume     numeric,
  source     text        not null default 'stooq',
  fetched_at timestamptz not null default now(),
  primary key (symbol, date, source)
);

create table public.holiday (
  -- A country code (Nager.Date) or an exchange MIC (closures from config).
  calendar   text        not null,
  date       date        not null,
  name       text        not null,
  local_name text,
  scope      text        not null check (scope in ('national', 'regional', 'exchange')),
  regions    text[]      not null default '{}',
  source     text        not null check (source in ('nager', 'config')),
  fetched_at timestamptz not null default now(),
  primary key (calendar, date, name)
);

create index holiday_date on public.holiday (date);

alter table public.fx_rate add column provider text;
comment on column public.fx_rate.provider is
  'Service that delivered the rate: cnb, ecb-data-portal or frankfurter (republishes ECB).';

alter table public.source drop constraint source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss', 'page', 'edgar', 'finnhub', 'google_news', 'reference'));

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.instrument_identifier enable row level security;
alter table public.listing enable row level security;
alter table public.listing_liquidity enable row level security;
alter table public.legal_entity enable row level security;
alter table public.entity_relationship enable row level security;
alter table public.entity_reporting_exception enable row level security;
alter table public.entity_alias enable row level security;
alter table public.price_bar enable row level security;
alter table public.holiday enable row level security;

revoke all on public.instrument_identifier, public.listing, public.listing_liquidity,
  public.legal_entity, public.entity_relationship, public.entity_reporting_exception,
  public.entity_alias, public.price_bar, public.holiday
  from anon, authenticated;
grant select on public.instrument_identifier, public.listing, public.listing_liquidity,
  public.legal_entity, public.entity_relationship, public.entity_reporting_exception,
  public.entity_alias, public.price_bar, public.holiday
  to authenticated;

create policy owner_read on public.instrument_identifier
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.listing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.listing_liquidity
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.legal_entity
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.entity_relationship
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.entity_reporting_exception
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.entity_alias
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.price_bar
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.holiday
  for select to authenticated using ((select private.is_owner()));

-- Backend: reference data is replaced as the sources change (identifiers, aliases and
-- config-sourced closures can disappear); relationships and prices are never deleted.
grant select, insert, update, delete on public.instrument_identifier, public.listing,
  public.listing_liquidity, public.legal_entity, public.entity_reporting_exception,
  public.entity_alias, public.holiday
  to app_backend;
grant select, insert, update on public.entity_relationship, public.price_bar to app_backend;

create policy backend_all on public.instrument_identifier
  for all to app_backend using (true) with check (true);
create policy backend_all on public.listing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.listing_liquidity
  for all to app_backend using (true) with check (true);
create policy backend_all on public.legal_entity
  for all to app_backend using (true) with check (true);
create policy backend_all on public.entity_relationship
  for all to app_backend using (true) with check (true);
create policy backend_all on public.entity_reporting_exception
  for all to app_backend using (true) with check (true);
create policy backend_all on public.entity_alias
  for all to app_backend using (true) with check (true);
create policy backend_all on public.price_bar
  for all to app_backend using (true) with check (true);
create policy backend_all on public.holiday
  for all to app_backend using (true) with check (true);
