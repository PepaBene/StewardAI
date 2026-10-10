-- Phase 8 (M8.2, M8.3): the source-to-screen audit ledger, the owner pilot and a page-use log.
-- `source_audit` is append-only: one row is one displayed fact checked against its source. A
-- correction is a new row, so the history of what was checked and what was wrong stays intact.

create table public.source_audit (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  subject_kind text not null
    check (subject_kind in ('security', 'etf', 'corporate_action', 'cash_flow_window')),
  subject text not null check (length(trim(subject)) between 1 and 80),
  region text check (region is null or length(trim(region)) between 1 and 40),
  feature text not null check (length(trim(feature)) between 1 and 60),
  field text not null check (length(trim(field)) between 1 and 120),
  displayed text not null check (length(displayed) <= 400),
  source_value text check (source_value is null or length(source_value) <= 400),
  source_url text check (source_url is null or length(source_url) <= 1000),
  result text not null check (result in ('match', 'mismatch', 'not_checkable')),
  note text check (note is null or length(note) <= 1000),
  audited_on date not null default current_date,
  created_at timestamptz not null default now(),
  check (result = 'not_checkable' or (source_value is not null and source_url is not null))
);
create index source_audit_owner on public.source_audit (owner_id, audited_on desc, id desc);
create index source_audit_subject on public.source_audit (owner_id, subject_kind, subject, field, id desc);

create table public.pilot_run (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  started_on date not null default current_date,
  length_days smallint not null default 30 check (length_days between 7 and 120),
  created_at timestamptz not null default now()
);
create index pilot_run_owner on public.pilot_run (owner_id, id desc);

create table public.usage_event (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  path text not null check (path ~ '^/[a-z0-9/_-]{0,120}$'),
  occurred_at timestamptz not null default now()
);
create index usage_event_owner on public.usage_event (owner_id, occurred_at desc);

alter table public.source_audit enable row level security;
alter table public.pilot_run enable row level security;
alter table public.usage_event enable row level security;
revoke all on public.source_audit, public.pilot_run, public.usage_event from anon;
grant select, insert on public.source_audit, public.pilot_run, public.usage_event
  to authenticated, app_backend;

create policy source_audit_owner_read on public.source_audit for select to authenticated
  using (owner_id = (select auth.uid()));
create policy source_audit_owner_insert on public.source_audit for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy source_audit_backend on public.source_audit for all to app_backend
  using (true) with check (true);
create policy pilot_run_owner_read on public.pilot_run for select to authenticated
  using (owner_id = (select auth.uid()));
create policy pilot_run_owner_insert on public.pilot_run for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy pilot_run_backend on public.pilot_run for all to app_backend
  using (true) with check (true);
create policy usage_event_owner_read on public.usage_event for select to authenticated
  using (owner_id = (select auth.uid()));
create policy usage_event_owner_insert on public.usage_event for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy usage_event_backend on public.usage_event for all to app_backend
  using (true) with check (true);

grant usage, select on sequence public.source_audit_id_seq, public.pilot_run_id_seq,
  public.usage_event_id_seq to authenticated, app_backend;

comment on table public.source_audit is
  'Manual source-to-screen audit: a displayed fact, the value in its cited source and the result. Append-only.';
comment on table public.pilot_run is 'Owner pilot windows; the newest row is the current pilot.';
comment on table public.usage_event is 'One row per page the owner opened, for the pilot usage measure only.';
