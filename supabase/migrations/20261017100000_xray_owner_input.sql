-- Owner-entered X-Ray inputs (plan Phase 5 gaps) for data no provider supplies here:
--   region_split   key = a region name such as "Europe"; payload = how the owner spreads it over
--                  countries (ISO-2 code -> share, summing to 1).
--   supply_edge    key = "SUPPLIER>CUSTOMER" (tickers); payload = the product and, optionally, the
--                  customer's share of the supplier's revenue.
--   index_weights  key = the index's name; payload = the constituents and weights the owner pasted
--                  from the index provider's factsheet.
-- Every row carries the source address and the date the figures describe, because nothing here is
-- looked up. Rows are never edited; the newest non-archived row per (kind, key) is the one used and
-- a row is archived (a flag), not deleted.

create table public.xray_owner_input (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('region_split', 'supply_edge', 'index_weights')),
  key text not null check (length(trim(key)) between 1 and 120),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_url text not null check (source_url ~* '^https?://'),
  as_of date not null,
  note text check (note is null or length(note) <= 500),
  -- 'derived' rows are computed from fund holdings the app already stores and are recomputed when
  -- those holdings are refreshed; derived_from names the fund they came from.
  origin text not null default 'owner' check (origin in ('owner', 'derived')),
  derived_from jsonb check (derived_from is null or jsonb_typeof(derived_from) = 'object'),
  archived boolean not null default false,
  created_at timestamptz not null default now()
);
create index xray_owner_input_owner on public.xray_owner_input (owner_id, kind, key, created_at desc);

alter table public.xray_owner_input enable row level security;
revoke all on public.xray_owner_input from anon;
grant select, insert, update (archived) on public.xray_owner_input to authenticated, app_backend;
create policy xray_owner_input_owner_all on public.xray_owner_input for all to authenticated
  using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy xray_owner_input_backend on public.xray_owner_input for all to app_backend
  using (true) with check (true);
grant usage, select on sequence public.xray_owner_input_id_seq to authenticated, app_backend;

comment on table public.xray_owner_input is
  'Owner-entered regional splits, supply-chain edges and index weights, each with a source and date; archived, never deleted.';
