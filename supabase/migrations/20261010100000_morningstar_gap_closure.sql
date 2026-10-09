-- Morningstar plan, gap closure for phases 0-3: owner-entered estimates and saved work that the
-- stored provider data cannot supply. Every table is owner-scoped and append-only: a correction
-- is a new row and the newest row wins, so an earlier result can be reproduced as recorded.

-- ---------------------------------------------------------------------------------------------
-- M2.12  Owner mapping of reported segments to a country, commodity, patent or trade context.
-- Always an estimate: the filing does not say it. `share` is the part of the segment's revenue
-- the owner assigns to that context (0 removes the mapping, 1 assigns all of it).
-- ---------------------------------------------------------------------------------------------
create table public.company_segment_map (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (length(ticker) between 1 and 32),
  breakdown text not null check (breakdown in ('geographic', 'segment', 'product')),
  member_label text not null check (length(trim(member_label)) between 1 and 300),
  context text not null check (context in ('country', 'commodity', 'patent_tech', 'trade_hs')),
  value text not null check (length(trim(value)) between 1 and 120),
  share numeric not null default 1 check (share >= 0 and share <= 1),
  note text check (note is null or length(note) <= 500),
  created_at timestamptz not null default now()
);
create index company_segment_map_latest
  on public.company_segment_map (owner_id, ticker, breakdown, member_label, context, value, id desc);

-- ---------------------------------------------------------------------------------------------
-- M2.9  Versioned company report: the code-computed sections as they stood, the research report
-- they were read beside, and the owner's conclusion. Each save is a new version.
-- ---------------------------------------------------------------------------------------------
create table public.company_report_version (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (length(ticker) between 1 and 32),
  version integer not null check (version >= 1),
  snapshot jsonb not null check (jsonb_typeof(snapshot) = 'object'),
  research_report_id bigint references public.report(id),
  conclusion text not null default '' check (length(conclusion) <= 8000),
  method_version text not null,
  input_hash text not null,
  created_at timestamptz not null default now(),
  unique (owner_id, ticker, version)
);

-- ---------------------------------------------------------------------------------------------
-- M3.5  Saved fund comparisons.
-- ---------------------------------------------------------------------------------------------
create table public.fund_comparison_saved (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(trim(name)) between 1 and 120),
  isins text[] not null check (cardinality(isins) between 2 and 5),
  note text check (note is null or length(note) <= 500),
  archived boolean not null default false,
  created_at timestamptz not null default now()
);
create index fund_comparison_saved_owner on public.fund_comparison_saved (owner_id, created_at desc);

-- ---------------------------------------------------------------------------------------------
-- M3.4  Owner-entered net asset values (the issuer's published NAV, with a source). Used only
-- to show premium or discount and a NAV return; the listing price is never presented as NAV.
-- ---------------------------------------------------------------------------------------------
create table public.fund_nav_point (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  fund_isin text not null check (fund_isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  as_of date not null,
  nav numeric not null check (nav > 0),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  source_url text not null check (length(source_url) between 8 and 2000),
  created_at timestamptz not null default now()
);
create index fund_nav_point_latest on public.fund_nav_point (owner_id, fund_isin, as_of, id desc);

-- ---------------------------------------------------------------------------------------------
-- M3.4 / M3.6 / M1.3  More owner-entered fund facts: the benchmark and its basis and currency,
-- and the bond statistics the stored issuer data do not carry.
-- ---------------------------------------------------------------------------------------------
alter table public.fund_owner_fact drop constraint fund_owner_fact_key_check;
alter table public.fund_owner_fact add constraint fund_owner_fact_key_check check (key in (
  'replication_method', 'securities_lending', 'lending_revenue_share', 'inception_date',
  'domicile', 'distribution_tax_note', 'liquidity_note', 'tracking_index', 'ucits',
  'benchmark_symbol', 'benchmark_currency', 'benchmark_basis',
  'average_maturity_years', 'effective_duration_years', 'credit_quality_note'));

-- ---------------------------------------------------------------------------------------------
-- Privileges and row-level security: owner reads and inserts their own rows; the backend role
-- does the same on the owner's behalf.
-- ---------------------------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array[
    'company_segment_map', 'company_report_version', 'fund_nav_point'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select, insert on public.%I to authenticated, app_backend', t);
    execute format(
      'create policy %I on public.%I for all to authenticated using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()))',
      t || '_owner', t);
    execute format(
      'create policy %I on public.%I for all to app_backend using (true) with check (true)',
      t || '_backend', t);
    execute format('grant usage, select on sequence public.%I to authenticated, app_backend', t || '_id_seq');
  end loop;
end $$;

-- Saved comparisons can be archived (a flag, never deleted).
alter table public.fund_comparison_saved enable row level security;
revoke all on public.fund_comparison_saved from anon;
grant select, insert, update (archived) on public.fund_comparison_saved to authenticated, app_backend;
create policy fund_comparison_saved_owner on public.fund_comparison_saved for all to authenticated
  using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy fund_comparison_saved_backend on public.fund_comparison_saved for all to app_backend
  using (true) with check (true);
grant usage, select on sequence public.fund_comparison_saved_id_seq to authenticated, app_backend;

comment on table public.company_segment_map is
  'Owner estimate mapping a reported segment to a country, commodity, patent or trade context; newest row per key wins, share 0 removes it.';
comment on table public.company_report_version is
  'Versioned company report snapshots with the owner''s conclusion; each save is a new version.';
comment on table public.fund_nav_point is
  'Owner-entered issuer NAV per date with a source; not provider data.';
