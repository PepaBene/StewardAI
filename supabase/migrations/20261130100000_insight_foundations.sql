-- Insight features plan, phase I0 (and the storage I1, I3, I6 need): the clocks that cannot be
-- reconstructed later, and the rows the source access tests record into.
--
-- 1. `fair_value_point` (I0.3): one row per owner, holding, basis and day; written by the daily
--    `fair_value_snapshot` job from existing valuation outputs. Append-only: a later correction is
--    a new day's row, never an edit of what the owner saw.
-- 2. `consensus_snapshot` (I0.4): analyst consensus as the tested endpoints return it, weekly. An
--    endpoint that is not accessible is recorded with its access state, never as zero.
-- 3. `dividend_forecast_snapshot` (I0.5): the forward schedule as the forecast stood that day, so
--    a later comparison with what was paid is possible (I1.6).
-- 4. `company_check_snapshot` (I3.9): the five-axis checks and risk checks, cached by input hash,
--    so the screener and the portfolio scorecard read the same result the company page shows.
-- 5. `company_valuation_scenario.method` (I6.2): `dcf` (every existing row) or `multiple_path`.
-- 6. The `checks` config domain (I3.10) and provider_access rows for the endpoints I0.2 tests.

create table public.fair_value_point (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (length(trim(ticker)) between 1 and 40),
  basis text not null check (basis in ('dcf_default', 'dcf_base', 'dcf_bear', 'dcf_bull', 'multiple_base',
    'multiple_bear', 'multiple_bull', 'thesis_target', 'peer_multiple', 'consensus_target')),
  value numeric not null check (value > 0),
  currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  price numeric check (price is null or price > 0),
  as_of date not null,
  known_at timestamptz not null default now(),
  scenario_id bigint references public.company_valuation_scenario(id),
  scenario_version integer,
  method_version text not null,
  input_hash text,
  inputs_as_of date,
  source text not null,
  detail jsonb not null default '{}' check (jsonb_typeof(detail) = 'object'),
  unique (owner_id, ticker, basis, as_of)
);
create index fair_value_point_series on public.fair_value_point (owner_id, ticker, basis, as_of desc);

create table public.consensus_snapshot (
  id bigint generated always as identity primary key,
  ticker text not null check (length(trim(ticker)) between 1 and 40),
  as_of date not null,
  source text not null,
  analyst_count integer check (analyst_count is null or analyst_count >= 0),
  target_mean numeric check (target_mean is null or target_mean > 0),
  target_median numeric check (target_median is null or target_median > 0),
  target_high numeric check (target_high is null or target_high > 0),
  target_low numeric check (target_low is null or target_low > 0),
  target_currency text check (target_currency is null or target_currency ~ '^[A-Z]{3}$'),
  eps_next_fy numeric,
  revenue_next_fy numeric,
  fiscal_period text,
  estimate_currency text check (estimate_currency is null or estimate_currency ~ '^[A-Z]{3}$'),
  price_at_snapshot numeric check (price_at_snapshot is null or price_at_snapshot > 0),
  known_at timestamptz not null default now(),
  access_state text not null check (access_state in ('complete', 'partial', 'unavailable', 'paid_only')),
  detail text,
  raw jsonb not null default '{}',
  unique (ticker, as_of, source)
);
create index consensus_snapshot_series on public.consensus_snapshot (ticker, as_of desc);

create table public.dividend_forecast_snapshot (
  id bigint generated always as identity primary key,
  ticker text not null check (length(trim(ticker)) between 1 and 40),
  as_of date not null,
  schedule jsonb not null check (jsonb_typeof(schedule) = 'object'),
  method_version text not null,
  input_hash text not null,
  known_at timestamptz not null default now(),
  unique (ticker, as_of, method_version)
);

create table public.company_check_snapshot (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (length(trim(ticker)) between 1 and 40),
  computed_at timestamptz not null default now(),
  method_version text not null,
  input_hash text not null,
  scores jsonb not null check (jsonb_typeof(scores) = 'object'),
  result jsonb not null check (jsonb_typeof(result) = 'object'),
  unique (owner_id, ticker, input_hash)
);
create index company_check_snapshot_latest on public.company_check_snapshot (owner_id, ticker, computed_at desc);

-- Append-only: what the owner saw stays as it was.
create function public.insight_history_immutable() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception '% rows are append-only', tg_table_name;
end $$;
create trigger fair_value_point_immutable before update on public.fair_value_point
  for each row execute function public.insight_history_immutable();
create trigger consensus_snapshot_immutable before update on public.consensus_snapshot
  for each row execute function public.insight_history_immutable();
create trigger dividend_forecast_snapshot_immutable before update on public.dividend_forecast_snapshot
  for each row execute function public.insight_history_immutable();

do $$
declare t text;
begin
  foreach t in array array['fair_value_point', 'company_check_snapshot'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format('grant select, insert on public.%I to app_backend', t);
    execute format(
      'create policy %I on public.%I for select to authenticated using (owner_id = (select auth.uid()))',
      t || '_owner_read', t);
    execute format('create policy %I on public.%I for all to app_backend using (true) with check (true)',
      t || '_backend', t);
    execute format('grant usage, select on sequence public.%I to app_backend', t || '_id_seq');
  end loop;
  foreach t in array array['consensus_snapshot', 'dividend_forecast_snapshot'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format('grant select, insert on public.%I to app_backend', t);
    execute format('create policy %I on public.%I for select to authenticated using (true)',
      t || '_read', t);
    execute format('create policy %I on public.%I for all to app_backend using (true) with check (true)',
      t || '_backend', t);
    execute format('grant usage, select on sequence public.%I to app_backend', t || '_id_seq');
  end loop;
end $$;
-- A cached check result is replaced by a newer input hash; old rows may be pruned.
grant delete on public.company_check_snapshot to app_backend;

comment on table public.fair_value_point is
  'Daily fair value per owner, holding and basis (DCF scenarios, multiple path, thesis, peer multiple, consensus). Append-only; history starts when the recorder starts (no back-fill).';
comment on table public.consensus_snapshot is
  'Analyst consensus as the free endpoints returned it on the day, with the access state when they did not. Append-only; never a synthetic value.';
comment on table public.dividend_forecast_snapshot is
  'The forward dividend schedule as forecast on the day (declared and estimated rows), for later accuracy checks. Append-only.';
comment on table public.company_check_snapshot is
  'Five-axis checks and risk checks computed from stored filings, ratios and prices; cached by input hash.';

-- I6.2: the scenario method. Every existing row is an explicit-year DCF.
alter table public.company_valuation_scenario
  add column method text not null default 'dcf' check (method in ('dcf', 'multiple_path'));
comment on column public.company_valuation_scenario.method is
  'Valuation method of the saved scenario set: dcf (explicit-year free cash flow) or multiple_path (earnings path to an exit P/E).';

-- I3.10: the rules domain.
alter table public.config_version drop constraint if exists config_version_domain_known;
alter table public.config_version add constraint config_version_domain_known check (domain in (
  'strategy', 'theses', 'holding_profiles', 'news_sources', 'event_rules',
  'notifications', 'research_profiles', 'verification', 'jobs', 'budget', 'prompts',
  'performance', 'checks'
));

-- I0.2: endpoints under test. Seeded unverified: the first request is the probe, a plan denial
-- latches paid_only, and nothing assumes an endpoint that has not passed.
insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked)
values
 ('yahoo','chart_dividends','access_unverified',null,null,null,1,'unofficial chart API, events=div; keyless probe',false),
 ('fmp','price_target_consensus','access_unverified','FMP_API_KEY',250,'day',1,'owner-key endpoint probe',false),
 ('finnhub','price_target','access_unverified','FINNHUB_API_KEY',null,null,1,'owner-key endpoint probe; documented as premium',false),
 ('twelve_data','price_target','access_unverified','TWELVE_DATA_API_KEY',800,'day',1,'owner-key endpoint probe; documented for higher plans',false)
on conflict (provider,endpoint) do nothing;

insert into public.provider_endpoint_manifest
  (provider,endpoint,path_template,response_unit,request_weight,production_consumer)
values
 ('yahoo','chart_dividends','/v8/finance/chart/{symbol}?events=div','dividend per share, listing currency',1,'second dividend source (suggested corporate actions)'),
 ('fmp','price_target_consensus','/stable/price-target-consensus','price per share',1,'consensus snapshot'),
 ('finnhub','price_target','/api/v1/stock/price-target','price per share',1,'consensus snapshot'),
 ('twelve_data','price_target','/price_target','price per share',1,'consensus snapshot')
on conflict (provider,endpoint) do nothing;
