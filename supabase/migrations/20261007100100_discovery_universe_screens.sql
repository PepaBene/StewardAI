-- Morningstar-style plan, Phase 1: investable universe, point-in-time universe snapshots and
-- saved screens.
--
-- 1. `investment_universe` (view): one row per Trading212 instrument with its identity chain
--    (instrument -> security -> issuer, listing), exchange, country, currency, asset type, status
--    and the data it has behind it. It copies no fact; it reads the existing identity tables.
-- 2. `universe_snapshot` / `universe_snapshot_member`: a frozen copy of the universe at a date,
--    kept with delisted members, so a past screen or backtest reads the membership known then.
-- 3. `screen_definition` + `screen_definition_version`: owner-saved screens; every edit adds an
--    immutable version. `screen_run` stores what a run saw (definition hash, as-of cutoff,
--    universe, counts, matched tickers) so it can be reproduced. `screen_column_set`: named
--    column sets.
--
-- Same access model as watchlist_item: RLS on, owner-only for the frontend, app_backend explicit.

create view public.investment_universe with (security_invoker = true) as
select
  i.t212_ticker                              as ticker,
  i.name,
  i.isin,
  i.instrument_type                          as instrument_type,
  coalesce(s.kind, i.instrument_type)        as asset_type,
  l.id                                       as listing_id,
  s.id                                       as security_id,
  s.issuer_id,
  coalesce(l.mic, i.home_mic)                as mic,
  i.exchange,
  coalesce(iss.country, i.home_country)      as country,
  upper(coalesce(l.currency, i.currency))    as currency,
  coalesce(l.status, 'active')               as status,
  l.valid_from                               as active_from,
  l.valid_to                                 as active_to,
  lnk.status                                 as identity_status,
  i.price_symbol,
  exists (select 1 from public.financial_statement_fact f where f.ticker = i.t212_ticker)
                                             as has_statements,
  case when i.price_symbol is null then false else exists
    (select 1 from public.price_bar b where b.symbol = i.price_symbol) end
                                             as has_prices,
  (select min(b.date) from public.price_bar b where b.symbol = i.price_symbol)
                                             as history_from
from public.instrument i
left join public.instrument_security_link lnk on lnk.ticker = i.t212_ticker
left join public.security s on s.id = lnk.security_id
left join public.security_listing l on l.id = lnk.listing_id
left join public.issuer iss on iss.id = s.issuer_id;

comment on view public.investment_universe is
  'Covered universe: every instrument the app knows, with identity chain, status and data availability. Not a market-wide universe.';

create table public.universe_snapshot (
  id bigint generated always as identity primary key,
  as_of date not null,
  known_at timestamptz not null default now(),
  source text not null default 'investment_universe',
  member_count integer not null default 0,
  active_count integer not null default 0,
  note text,
  unique (as_of, source)
);

create table public.universe_snapshot_member (
  snapshot_id bigint not null references public.universe_snapshot(id) on delete cascade,
  ticker text not null,
  security_id bigint,
  listing_id bigint,
  issuer_id bigint,
  isin text,
  name text,
  asset_type text,
  mic text,
  exchange text,
  country text,
  currency text,
  status text not null,
  active_from date,
  active_to date,
  inclusion_reason text not null,
  primary key (snapshot_id, ticker)
);
create index universe_snapshot_member_ticker on public.universe_snapshot_member (ticker);

create table public.screen_definition (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(trim(name)) between 1 and 120),
  kind text not null check (kind in ('stock', 'fund')),
  latest_version integer not null default 1,
  archived boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, kind, name)
);

create table public.screen_definition_version (
  screen_id bigint not null references public.screen_definition(id) on delete cascade,
  version integer not null check (version >= 1),
  definition jsonb not null,
  definition_hash text not null,
  method_version text not null,
  created_at timestamptz not null default now(),
  primary key (screen_id, version)
);

create table public.screen_run (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  screen_id bigint references public.screen_definition(id) on delete set null,
  screen_version integer,
  kind text not null check (kind in ('stock', 'fund')),
  definition_hash text not null,
  method_version text not null,
  as_of_cutoff timestamptz,
  universe_key text not null,
  universe_snapshot_id bigint references public.universe_snapshot(id) on delete set null,
  universe_size integer not null,
  result_count integer not null,
  unknown_count integer not null default 0,
  excluded_count integer not null default 0,
  matched_tickers text[] not null default '{}',
  result_hash text not null,
  run_at timestamptz not null default now()
);
create index screen_run_owner on public.screen_run (owner_id, run_at desc);

create table public.screen_column_set (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('stock', 'fund')),
  name text not null check (length(trim(name)) between 1 and 80),
  columns text[] not null check (cardinality(columns) between 1 and 40),
  created_at timestamptz not null default now(),
  unique (owner_id, kind, name)
);

-- Access: owner-only tables.
do $$
declare t text;
begin
  foreach t in array array['screen_definition', 'screen_run', 'screen_column_set'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
    execute format('grant select, insert, update, delete on public.%I to app_backend', t);
    execute format(
      'create policy %I on public.%I for all to authenticated using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()))',
      t || '_owner_all', t);
    execute format('create policy %I on public.%I for all to app_backend using (true) with check (true)',
      t || '_backend_all', t);
  end loop;
  foreach t in array array['screen_definition', 'screen_run', 'screen_column_set'] loop
    execute format('grant usage, select on sequence public.%I to authenticated, app_backend', t || '_id_seq');
  end loop;

  -- Versions follow their screen's owner through the parent row.
  alter table public.screen_definition_version enable row level security;
  revoke all on public.screen_definition_version from anon, authenticated;
  grant select on public.screen_definition_version to authenticated;
  grant select, insert, update, delete on public.screen_definition_version to app_backend;
  create policy screen_version_owner_select on public.screen_definition_version
    for select to authenticated
    using (exists (select 1 from public.screen_definition d
                   where d.id = screen_id and d.owner_id = (select auth.uid())));
  create policy screen_version_backend_all on public.screen_definition_version
    for all to app_backend using (true) with check (true);

  foreach t in array array['universe_snapshot', 'universe_snapshot_member'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format('grant select, insert, update, delete on public.%I to app_backend', t);
    execute format('create policy %I on public.%I for select to authenticated using (true)', t || '_read', t);
    execute format('create policy %I on public.%I for all to app_backend using (true) with check (true)',
      t || '_backend_all', t);
  end loop;
  grant usage, select on sequence public.universe_snapshot_id_seq to app_backend;
end $$;

revoke all on public.investment_universe from anon, authenticated;
grant select on public.investment_universe to app_backend;

-- A saved definition version is immutable: a screen edit adds a version, never rewrites one.
create function private.screen_version_immutable() returns trigger
language plpgsql as $$
begin
  raise exception 'screen_definition_version rows are immutable';
end $$;
create trigger screen_definition_version_immutable
  before update on public.screen_definition_version
  for each row execute function private.screen_version_immutable();

create trigger screen_definition_set_updated_at
  before update on public.screen_definition
  for each row execute function private.set_updated_at();
