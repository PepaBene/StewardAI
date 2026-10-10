-- Fund and ETF analysis workbench (plan Phase 3): facts the fund's issuer data do not carry
-- (replication method, securities lending, inception, domicile, tax and liquidity notes),
-- entered by the owner with a source. Reported fund figures are never stored here.
--
-- Append-only: a correction is a new row and the newest row per key wins, so an earlier
-- checklist can be reproduced from what was recorded at the time.

create table public.fund_owner_fact (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  fund_isin text not null check (fund_isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  key text not null check (key in (
    'replication_method', 'securities_lending', 'lending_revenue_share', 'inception_date',
    'domicile', 'distribution_tax_note', 'liquidity_note', 'tracking_index', 'ucits')),
  value text not null check (length(trim(value)) between 1 and 500),
  source_url text not null check (length(source_url) between 8 and 2000),
  passage text check (passage is null or length(passage) <= 1000),
  as_of date,
  created_at timestamptz not null default now()
);
create index fund_owner_fact_latest on public.fund_owner_fact (owner_id, fund_isin, key, id desc);

alter table public.fund_owner_fact enable row level security;
revoke all on public.fund_owner_fact from anon;
grant select, insert on public.fund_owner_fact to authenticated;
grant select, insert on public.fund_owner_fact to app_backend;
create policy fund_owner_fact_owner on public.fund_owner_fact for all to authenticated
  using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy fund_owner_fact_backend on public.fund_owner_fact for all to app_backend
  using (true) with check (true);
grant usage, select on sequence public.fund_owner_fact_id_seq to authenticated, app_backend;

comment on table public.fund_owner_fact is
  'Owner-entered, sourced fund facts for the due-diligence checklist; append-only, newest per key wins.';
