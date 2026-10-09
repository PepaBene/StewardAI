-- Saved analysis (plan Phase 4/5 gaps): "what if" X-Ray scenarios and dated performance report
-- snapshots the owner chose to keep.
--   xray_scenario       payload = the proposed weights and additions; it is run again against the
--                       holdings of the day it is opened, so it never shows stale holdings.
--   performance_report  payload = the report as it was shown, kept as it stood (figures, ledger
--                       health and the method version), so a past figure can be shown again.
-- Rows are never edited; a saved item is archived (a flag), not deleted.

create table public.saved_analysis (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('xray_scenario', 'performance_report')),
  name text not null check (length(trim(name)) between 1 and 120),
  note text check (note is null or length(note) <= 500),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  method_version text,
  as_of date,
  archived boolean not null default false,
  created_at timestamptz not null default now()
);
create index saved_analysis_owner on public.saved_analysis (owner_id, kind, created_at desc);

alter table public.saved_analysis enable row level security;
revoke all on public.saved_analysis from anon;
grant select, insert, update (archived) on public.saved_analysis to authenticated, app_backend;
create policy saved_analysis_owner_all on public.saved_analysis for all to authenticated
  using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy saved_analysis_backend on public.saved_analysis for all to app_backend
  using (true) with check (true);
grant usage, select on sequence public.saved_analysis_id_seq to authenticated, app_backend;

comment on table public.saved_analysis is
  'Owner-saved X-Ray what-if scenarios and performance report snapshots; archived, never deleted.';
