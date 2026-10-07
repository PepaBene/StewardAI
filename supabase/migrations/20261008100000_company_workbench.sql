-- Company analysis workbench (plan Phase 2): saved peer groups, saved valuation scenarios and
-- cited investment-case items. Reported figures are never stored here; these are the owner's
-- choices and assessments, kept apart from facts.

create table public.company_peer_group (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null,
  name text not null check (length(trim(name)) between 1 and 80),
  peers text[] not null check (cardinality(peers) between 1 and 40),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, ticker, name)
);

-- A saved scenario set is append-only: a change is a new version, and the result it produced
-- keeps the method version and input hash it was computed under.
create table public.company_valuation_scenario (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null,
  name text not null check (length(trim(name)) between 1 and 80),
  version integer not null check (version >= 1),
  assumptions jsonb not null,
  method_version text not null,
  input_hash text,
  note text,
  created_at timestamptz not null default now(),
  unique (owner_id, ticker, name, version)
);

create table public.company_case_item (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null,
  kind text not null check (kind in
    ('guidance', 'moat', 'bull', 'bear', 'concentration', 'capital_allocation', 'risk')),
  moat_type text check (moat_type is null or moat_type in
    ('switching_costs', 'scale', 'ip', 'network_effects', 'regulation', 'none')),
  statement text not null check (length(trim(statement)) between 3 and 2000),
  evidence jsonb not null default '[]',
  confidence text not null default 'low' check (confidence in ('low', 'medium', 'high')),
  status text not null default 'draft' check (status in ('draft', 'confirmed')),
  archived boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((kind = 'moat') = (moat_type is not null)),
  -- A confirmed assessment cites at least one source; a draft may not yet.
  check (status = 'draft' or jsonb_array_length(evidence) > 0)
);
create index company_case_item_ticker on public.company_case_item (owner_id, ticker, kind)
  where not archived;

do $$
declare t text;
begin
  foreach t in array array['company_peer_group', 'company_valuation_scenario', 'company_case_item'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
    execute format('grant select, insert, update, delete on public.%I to app_backend', t);
    execute format(
      'create policy %I on public.%I for all to authenticated using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()))',
      t || '_owner', t);
    execute format('create policy %I on public.%I for all to app_backend using (true) with check (true)',
      t || '_backend', t);
    execute format('grant usage, select on sequence public.%I to authenticated, app_backend', t || '_id_seq');
  end loop;
end $$;

-- Saved scenario versions are immutable.
create function public.company_valuation_scenario_immutable() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'a saved valuation scenario version is immutable; save a new version';
end $$;
create trigger company_valuation_scenario_no_update
  before update on public.company_valuation_scenario
  for each row execute function public.company_valuation_scenario_immutable();
