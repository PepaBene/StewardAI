-- Saved risk reports (quant plan engineering rule 5): the ex-ante risk report, the past-episode
-- replay, the factor exposures and the factor-shock runs of saved scenarios as the owner saw them, with their method version, so a later
-- change to a model never rewrites what was shown. One row per report kind, scope (the factor
-- region, empty otherwise), data date and method version; written by the API and the sync,
-- never changed afterwards.

create table public.risk_report_snapshot (
  id bigint generated always as identity primary key,
  kind text not null check (kind in ('ex_ante', 'replay', 'factors', 'shock')),
  scope text not null default '',
  as_of date not null,
  method_version text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  unique (kind, scope, as_of, method_version)
);

alter table public.risk_report_snapshot enable row level security;
revoke all on public.risk_report_snapshot from anon, authenticated;
grant select on public.risk_report_snapshot to authenticated;
grant select, insert on public.risk_report_snapshot to app_backend;
create policy owner_read on public.risk_report_snapshot for select to authenticated
  using ((select private.is_owner()));
create policy backend_insert on public.risk_report_snapshot for insert to app_backend
  with check (true);
create policy backend_read on public.risk_report_snapshot for select to app_backend
  using (true);

comment on table public.risk_report_snapshot is
  'Append-only copies of computed risk reports with their method version (quant plan rule 5).';
