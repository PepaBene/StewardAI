-- Phase 7-8 follow-up (M7.5, M8.3): snoozing a data-quality issue and a pilot defect log.
-- Both tables are append-only. A snooze is ended by a newer 'clear' row for the same issue; a
-- defect changes state through a newer row that points at the first one, so what was reported and
-- when it was closed stays on record.

create table public.quality_snooze (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (length(trim(kind)) between 1 and 40),
  subject text not null check (length(trim(subject)) between 1 and 120),
  action text not null check (action in ('snooze', 'clear')),
  until date,
  note text check (note is null or length(note) <= 500),
  created_at timestamptz not null default now(),
  check (action = 'clear' or until is not null)
);
create index quality_snooze_issue on public.quality_snooze (owner_id, kind, subject, id desc);

create table public.pilot_defect (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  parent_id bigint references public.pilot_defect(id),
  title text not null check (length(trim(title)) between 1 and 160),
  detail text check (detail is null or length(detail) <= 2000),
  ticker text check (ticker is null or length(trim(ticker)) between 1 and 32),
  feature text check (feature is null or length(trim(feature)) between 1 and 60),
  state text not null default 'open' check (state in ('open', 'fixed', 'wont_fix')),
  created_at timestamptz not null default now()
);
create index pilot_defect_owner on public.pilot_defect (owner_id, id desc);

alter table public.quality_snooze enable row level security;
alter table public.pilot_defect enable row level security;
revoke all on public.quality_snooze, public.pilot_defect from anon;
grant select, insert on public.quality_snooze, public.pilot_defect to authenticated, app_backend;

create policy quality_snooze_owner_read on public.quality_snooze for select to authenticated
  using (owner_id = (select auth.uid()));
create policy quality_snooze_owner_insert on public.quality_snooze for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy quality_snooze_backend on public.quality_snooze for all to app_backend
  using (true) with check (true);
create policy pilot_defect_owner_read on public.pilot_defect for select to authenticated
  using (owner_id = (select auth.uid()));
create policy pilot_defect_owner_insert on public.pilot_defect for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy pilot_defect_backend on public.pilot_defect for all to app_backend
  using (true) with check (true);
